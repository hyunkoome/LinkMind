"""backend/api/graph.py 의 pure 함수 단위 테스트 (D10.5 세션 B — keyword ▸ wiki ▸ item).

cytoscape JSON 변환 + _build_kwi dedup/dangling 로직 검증. DB 호출은 integration
으로 별도 (여기는 row dict → GraphNode/Edge 변환만).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from backend.api.graph import (
    _build_kwi,
    _short_summary,
    item_to_node,
    keyword_keyword_edge,
    keyword_to_node,
    keyword_wiki_edge,
    wiki_item_edge,
    wiki_to_node,
)


# ── keyword_to_node ──────────────────────────────────────────


def test_keyword_to_node_basic():
    node = keyword_to_node("3dgs", 42)
    assert node.data["id"] == "keyword:3dgs"
    assert node.data["type"] == "keyword"
    assert node.data["label"] == "3dgs"
    assert node.data["slug"] == "3dgs"
    assert node.data["wiki_count"] == 42


def test_keyword_to_node_zero_count():
    assert keyword_to_node("slam", 0).data["wiki_count"] == 0


# ── wiki_to_node ─────────────────────────────────────────────


def test_wiki_to_node_basic():
    wiki = {
        "id": UUID("11111111-1111-1111-1111-111111111111"),
        "slug": "arxiv__2106-09685",
        "title": "LoRA: Low-Rank Adaptation",
        "keywords": ["lora", "transformer"],
        "primary_external_id": {"kind": "arxiv", "value": "2106.09685"},
        "item_count": 3,
    }
    node = wiki_to_node(wiki)
    assert node.data["id"] == "wiki:arxiv__2106-09685"
    assert node.data["type"] == "wiki"
    assert node.data["label"] == "LoRA: Low-Rank Adaptation"
    assert node.data["slug"] == "arxiv__2106-09685"
    assert node.data["item_count"] == 3
    assert node.data["keywords"] == ["lora", "transformer"]
    assert node.data["primary_external_id"]["kind"] == "arxiv"


def test_wiki_to_node_fallback_label_and_missing_fields():
    """title 없으면 slug, keywords/primary_external_id/item_count 없어도 OK."""
    w = {"id": uuid4(), "slug": "url__item__abc", "title": None}
    node = wiki_to_node(w)
    assert node.data["label"] == "url__item__abc"
    assert node.data["keywords"] == []
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
    long = "가" * 500
    item = {
        "id": uuid4(), "source_type": "url", "title": "t",
        "summary": long, "ingested_at": datetime(2026, 5, 18),
    }
    s = item_to_node(item).data["summary"]
    assert s.endswith("…")
    assert len(s) <= 210


def test_item_to_node_handles_none_summary_and_ingested():
    item = {
        "id": uuid4(), "source_type": "url", "title": "t",
        "summary": None, "ingested_at": None,
    }
    node = item_to_node(item)
    assert node.data["summary"] is None
    assert node.data["ingested_at"] is None


# ── edges ────────────────────────────────────────────────────


def test_keyword_wiki_edge():
    e = keyword_wiki_edge("3dgs", "arxiv__x")
    assert e.data["id"] == "edge:kw:3dgs:arxiv__x"
    assert e.data["source"] == "keyword:3dgs"
    assert e.data["target"] == "wiki:arxiv__x"
    assert e.data["role"] == "keyword"


def test_keyword_keyword_edge_id_sorted():
    """co-occurrence 엣지 — id 는 정렬(dedup), source/target 은 원래 방향."""
    e = keyword_keyword_edge("slam", "ai", 5)
    assert e.data["id"] == "edge:kwkw:ai:slam"  # 정렬
    assert e.data["source"] == "keyword:slam"
    assert e.data["target"] == "keyword:ai"
    assert e.data["role"] == "cooccur"
    assert e.data["confidence"] == 5.0
    # 반대 방향 호출도 같은 id (dedup)
    e2 = keyword_keyword_edge("ai", "slam", 5)
    assert e2.data["id"] == e.data["id"]


def test_wiki_item_edge_basic_and_defaults():
    iid = UUID("22222222-2222-2222-2222-222222222222")
    e = wiki_item_edge("yt__abc", iid, {"role": "figure", "confidence": 0.7, "source": "auto"})
    assert e.data["id"] == f"edge:wiki:yt__abc:{iid}"
    assert e.data["source"] == "wiki:yt__abc"
    assert e.data["target"] == f"item:{iid}"
    assert e.data["role"] == "figure"
    assert e.data["confidence"] == 0.7
    # 기본값
    e2 = wiki_item_edge("yt__abc", iid, {})
    assert e2.data["role"] == "source"
    assert e2.data["confidence"] == 1.0
    assert e2.data["link_source"] == "auto"


# ── _build_kwi (조립 + dedup + dangling 방지) ────────────────


def _wiki(uid: UUID, slug: str):
    return {"id": uid, "slug": slug, "title": slug, "keywords": [], "item_count": 1}


def _item(uid: UUID):
    return {"id": uid, "source_type": "url", "title": "i", "ingested_at": datetime(2026, 5, 18)}


def test_build_kwi_keyword_wiki_item_full():
    wid = uuid4()
    iid = uuid4()
    wikis = [_wiki(wid, "w1")]
    items = [_item(iid)]
    links = [{"wiki_page_id": wid, "item_id": iid, "role": "primary"}]
    res = _build_kwi(keyword_to_node("kw", 1), wikis, items, links, {wid: wikis[0]})
    # 노드: keyword + wiki + item = 3
    assert len(res.nodes) == 3
    types = {n.data["type"] for n in res.nodes}
    assert types == {"keyword", "wiki", "item"}
    # 엣지: keyword→wiki + wiki→item = 2
    assert len(res.edges) == 2
    roles = {e.data["role"] for e in res.edges}
    assert "keyword" in roles


def test_build_kwi_without_keyword_node():
    wid = uuid4()
    iid = uuid4()
    wikis = [_wiki(wid, "w1")]
    links = [{"wiki_page_id": wid, "item_id": iid}]
    res = _build_kwi(None, wikis, [_item(iid)], links, {wid: wikis[0]})
    assert all(n.data["type"] != "keyword" for n in res.nodes)
    # keyword→wiki 엣지 없음, wiki→item 만
    assert len(res.edges) == 1
    assert res.edges[0].data["role"] == "source"


def test_build_kwi_skips_dangling_item_edge():
    """link 의 item 이 items 에 없으면 그 엣지 skip (frontend node-not-found 방지)."""
    wid = uuid4()
    iid_present = uuid4()
    iid_missing = uuid4()
    wikis = [_wiki(wid, "w1")]
    links = [
        {"wiki_page_id": wid, "item_id": iid_present},
        {"wiki_page_id": wid, "item_id": iid_missing},  # items 에 없음
    ]
    res = _build_kwi(None, wikis, [_item(iid_present)], links, {wid: wikis[0]})
    # 존재하는 item 엣지 1개만
    assert len(res.edges) == 1
    assert res.edges[0].data["target"] == f"item:{iid_present}"


def test_build_kwi_skips_link_with_unknown_wiki():
    """wiki_by_id 에 없는 link 는 skip."""
    iid = uuid4()
    res = _build_kwi(None, [], [_item(iid)], [{"wiki_page_id": uuid4(), "item_id": iid}], {})
    assert res.edges == []
    # item 노드는 표시
    assert len(res.nodes) == 1


def test_build_kwi_dedups_nodes():
    wid = uuid4()
    iid = uuid4()
    wikis = [_wiki(wid, "w1"), _wiki(wid, "w1")]  # 중복
    res = _build_kwi(None, wikis, [_item(iid), _item(iid)], [], {wid: wikis[0]})
    assert len(res.nodes) == 2  # wiki 1 + item 1


def test_build_kwi_empty():
    res = _build_kwi(None, [], [], [], {})
    assert res.nodes == []
    assert res.edges == []


# ── _short_summary ──────────────────────────────────────────


def test_short_summary_below_max_unchanged():
    assert _short_summary("hello") == "hello"
    assert _short_summary("  hello  ") == "hello"


def test_short_summary_above_max_truncated_with_ellipsis():
    out = _short_summary("a" * 300, max_chars=100)
    assert out.endswith("…")
    assert len(out) <= 102


def test_short_summary_none_returns_none():
    assert _short_summary(None) is None
    assert _short_summary("") is None
