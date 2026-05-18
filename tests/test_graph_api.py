"""backend/api/graph.py 의 pure 함수 단위 테스트.

cytoscape JSON 변환 + dedup 로직 검증. DB 호출은 integration 테스트로 별도
(여기는 row dict → GraphNode/Edge 변환만).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from backend.api.graph import (
    _short_summary,
    build_graph_response,
    item_to_node,
    link_to_edge,
    topic_to_node,
)


# ── topic_to_node ────────────────────────────────────────────


def test_topic_to_node_basic():
    topic = {
        "id": UUID("11111111-1111-1111-1111-111111111111"),
        "slug": "arxiv:2106.09685",
        "title": "LoRA: Low-Rank Adaptation",
        "primary_external_id": {"kind": "arxiv", "value": "2106.09685"},
        "tags": ["LoRA", "Transformer"],
        "item_count": 3,
    }
    node = topic_to_node(topic)
    assert node.data["id"] == "topic:11111111-1111-1111-1111-111111111111"
    assert node.data["type"] == "topic"
    assert node.data["label"] == "LoRA: Low-Rank Adaptation"
    assert node.data["slug"] == "arxiv:2106.09685"
    assert node.data["item_count"] == 3
    assert node.data["tags"] == ["LoRA", "Transformer"]
    assert node.data["primary_external_id"]["kind"] == "arxiv"


def test_topic_to_node_fallback_label_to_slug():
    """title 없으면 slug, 둘 다 없으면 '(untitled)'."""
    t1 = {"id": uuid4(), "slug": "tag:CV", "title": None}
    assert topic_to_node(t1).data["label"] == "tag:CV"

    t2 = {"id": uuid4(), "slug": None, "title": None}
    assert topic_to_node(t2).data["label"] == "(untitled)"


def test_topic_to_node_handles_missing_optional_fields():
    """tags / primary_external_id / item_count 없어도 OK."""
    t = {"id": uuid4(), "slug": "x", "title": "t"}
    node = topic_to_node(t)
    assert node.data["tags"] == []
    assert node.data["primary_external_id"] == {}
    assert node.data["item_count"] == 0


# ── item_to_node ─────────────────────────────────────────────


def test_item_to_node_basic():
    item = {
        "id": UUID("22222222-2222-2222-2222-222222222222"),
        "source_type": "pdf",
        "source_url": "/files/abc123",
        "title": "포인트클라우드 압축 논문",
        "summary": "이 논문은 ...",
        "tags": ["PDF", "압축"],
        "is_read": False,
        "has_notes": True,
        "ingested_at": datetime(2026, 5, 18, 10, 0, 0),
    }
    node = item_to_node(item)
    assert node.data["id"] == "item:22222222-2222-2222-2222-222222222222"
    assert node.data["type"] == "item"
    assert node.data["label"] == "포인트클라우드 압축 논문"
    assert node.data["source_type"] == "pdf"
    assert node.data["is_read"] is False
    assert node.data["has_notes"] is True
    assert node.data["tags"] == ["PDF", "압축"]
    assert node.data["ingested_at"] == "2026-05-18T10:00:00"


def test_item_to_node_label_fallback_url_then_source_type():
    """title 없으면 url 짧게, url 도 없으면 source_type."""
    i1 = {
        "id": uuid4(), "source_type": "url", "title": None,
        "source_url": "https://example.com/very/long/path/to/article",
        "ingested_at": datetime(2026, 5, 18),
    }
    assert "example.com" in item_to_node(i1).data["label"]

    i2 = {
        "id": uuid4(), "source_type": "telegram", "title": None, "source_url": None,
        "ingested_at": datetime(2026, 5, 18),
    }
    assert item_to_node(i2).data["label"] == "telegram"


def test_item_to_node_summary_truncated():
    """summary 가 200자 넘으면 … 로 cut."""
    long = "가" * 500
    item = {
        "id": uuid4(), "source_type": "url", "title": "t",
        "summary": long, "ingested_at": datetime(2026, 5, 18),
    }
    node = item_to_node(item)
    s = node.data["summary"]
    assert s.endswith("…")
    assert len(s) <= 210


def test_item_to_node_handles_none_summary():
    item = {
        "id": uuid4(), "source_type": "url", "title": "t",
        "summary": None, "ingested_at": datetime(2026, 5, 18),
    }
    assert item_to_node(item).data["summary"] is None


def test_item_to_node_handles_no_ingested_at():
    item = {"id": uuid4(), "source_type": "url", "title": "t", "ingested_at": None}
    assert item_to_node(item).data["ingested_at"] is None


# ── link_to_edge ─────────────────────────────────────────────


def test_link_to_edge_basic():
    iid = UUID("22222222-2222-2222-2222-222222222222")
    tid = UUID("11111111-1111-1111-1111-111111111111")
    link = {
        "item_id": iid, "topic_id": tid,
        "role": "paper", "confidence": 0.9, "source": "auto",
    }
    edge = link_to_edge(link)
    assert edge.data["id"] == f"edge:{iid}:{tid}"
    assert edge.data["source"] == f"item:{iid}"
    assert edge.data["target"] == f"topic:{tid}"
    assert edge.data["role"] == "paper"
    assert edge.data["confidence"] == 0.9
    assert edge.data["link_source"] == "auto"


def test_link_to_edge_defaults():
    """role/confidence/source 빠지면 안전 default."""
    link = {"item_id": uuid4(), "topic_id": uuid4()}
    edge = link_to_edge(link)
    assert edge.data["role"] == "item"
    assert edge.data["confidence"] == 1.0
    assert edge.data["link_source"] == "auto"


# ── build_graph_response (dedup) ─────────────────────────────


def _sample_topic(uid: UUID, label: str = "t"):
    return {"id": uid, "slug": label, "title": label, "tags": []}


def _sample_item(uid: UUID, label: str = "i"):
    return {
        "id": uid, "source_type": "url", "title": label,
        "ingested_at": datetime(2026, 5, 18),
    }


def test_build_graph_response_combines_all():
    tid = uuid4()
    iid = uuid4()
    res = build_graph_response(
        topics=[_sample_topic(tid, "topic A")],
        items=[_sample_item(iid, "item A")],
        links=[{"item_id": iid, "topic_id": tid, "role": "paper"}],
    )
    assert len(res.nodes) == 2
    assert len(res.edges) == 1


def test_build_graph_response_dedups_duplicate_nodes():
    """같은 id 가 여러 path 로 와도 한 번만 (graph_item neighborhood 자주 발생)."""
    iid = uuid4()
    res = build_graph_response(
        topics=[],
        items=[_sample_item(iid), _sample_item(iid)],   # 중복
        links=[],
    )
    assert len(res.nodes) == 1


def test_build_graph_response_dedups_duplicate_edges():
    """같은 (item, topic) link 가 두 번 들어와도 한 엣지."""
    iid = uuid4()
    tid = uuid4()
    link = {"item_id": iid, "topic_id": tid, "role": "paper"}
    res = build_graph_response(
        topics=[_sample_topic(tid)],
        items=[_sample_item(iid)],
        links=[link, link],
    )
    assert len(res.edges) == 1


def test_build_graph_response_empty():
    res = build_graph_response([], [], [])
    assert res.nodes == []
    assert res.edges == []


# ── _short_summary ──────────────────────────────────────────


def test_short_summary_below_max_unchanged():
    assert _short_summary("hello") == "hello"
    assert _short_summary("  hello  ") == "hello"


def test_short_summary_above_max_truncated_with_ellipsis():
    s = "a" * 300
    out = _short_summary(s, max_chars=100)
    assert out.endswith("…")
    assert len(out) <= 102   # 100 + … + 약간 trim


def test_short_summary_none_returns_none():
    assert _short_summary(None) is None
    assert _short_summary("") is None
