"""
GET /graph/topics, GET /graph/search, GET /graph/item/{item_id}

cytoscape.js 호환 JSON ({nodes: [...], edges: [...]}) 반환. frontend_v2 의 graph UI
(Phase 2.5+) 가 이 endpoint 만 호출해서 데이터 받음.

설계:
- 노드 종류 2개: topic (cluster 표현) + item (실 자료)
- 엣지 한 종류: item ↔ topic, role 속성으로 modality (paper/code/video/...) 표시
- raw_content 같은 큰 필드는 노드 data 에 X — GET /items/{id} 따로 호출
  (graph 는 가벼움 우선, 노드 클릭 시 details 별 fetch)
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.connection import get_session
from backend.db.repository import (
    get_item_full,
    list_item_topic_links,
    list_items_for_topic,
    list_items_summary,
    list_topics,
    list_topics_for_item,
    search_items_by_text,
)
from backend.schemas.models import GraphEdge, GraphNode, GraphResponse

logger = logging.getLogger(__name__)
router = APIRouter()


# ──────────────────────────────────────────────────────────────
# cytoscape 변환 helper (pure 함수 — tests/ 에서 직접 unit)
# ──────────────────────────────────────────────────────────────


def topic_to_node(topic: dict[str, Any]) -> GraphNode:
    """topic row → cytoscape 노드. id 는 'topic:<uuid>' prefix.

    label: title 우선, 없으면 slug.
    """
    primary_ext = topic.get("primary_external_id") or {}
    return GraphNode(
        data={
            "id": f"topic:{topic['id']}",
            "label": topic.get("title") or topic.get("slug") or "(untitled)",
            "type": "topic",
            "slug": topic.get("slug"),
            "title": topic.get("title"),
            "item_count": int(topic.get("item_count") or 0),
            "primary_external_id": primary_ext,
            "tags": list(topic.get("tags") or []),
        }
    )


def item_to_node(item: dict[str, Any]) -> GraphNode:
    """item row → cytoscape 노드. id 는 'item:<uuid>' prefix.

    label: title 우선, 없으면 source_url 짧게, 없으면 source_type.
    """
    label = item.get("title")
    if not label:
        url = item.get("source_url") or ""
        if url:
            label = (url.split("//")[-1])[:60]
        else:
            label = item.get("source_type") or "(item)"
    return GraphNode(
        data={
            "id": f"item:{item['id']}",
            "label": label,
            "type": "item",
            "source_type": item.get("source_type"),
            "source_url": item.get("source_url"),
            "title": item.get("title"),
            "summary": _short_summary(item.get("summary")),
            "tags": list(item.get("tags") or []),
            "is_read": bool(item.get("is_read")),
            "has_notes": bool(item.get("has_notes")),
            "ingested_at": item["ingested_at"].isoformat()
            if item.get("ingested_at") else None,
        }
    )


def link_to_edge(link: dict[str, Any]) -> GraphEdge:
    """item_topics row → cytoscape 엣지. id 는 'edge:<item>:<topic>'."""
    item_id = link["item_id"]
    topic_id = link["topic_id"]
    return GraphEdge(
        data={
            "id": f"edge:{item_id}:{topic_id}",
            "source": f"item:{item_id}",
            "target": f"topic:{topic_id}",
            "role": link.get("role") or "item",
            "confidence": float(link.get("confidence") or 1.0),
            "link_source": link.get("source") or "auto",
        }
    )


def _short_summary(summary: str | None, *, max_chars: int = 200) -> str | None:
    if not summary:
        return None
    s = summary.strip()
    if len(s) <= max_chars:
        return s
    return s[:max_chars].rstrip() + "…"


def build_graph_response(
    topics: list[dict[str, Any]],
    items: list[dict[str, Any]],
    links: list[dict[str, Any]],
) -> GraphResponse:
    """3개 입력 → cytoscape JSON. id 중복 자동 제거 (같은 노드가 여러 path 로 와도 안전)."""
    seen_ids: set[str] = set()
    nodes: list[GraphNode] = []
    for t in topics:
        n = topic_to_node(t)
        if n.data["id"] not in seen_ids:
            seen_ids.add(n.data["id"])
            nodes.append(n)
    for it in items:
        n = item_to_node(it)
        if n.data["id"] not in seen_ids:
            seen_ids.add(n.data["id"])
            nodes.append(n)

    edges: list[GraphEdge] = []
    seen_edges: set[str] = set()
    for lk in links:
        e = link_to_edge(lk)
        if e.data["id"] not in seen_edges:
            seen_edges.add(e.data["id"])
            edges.append(e)

    return GraphResponse(nodes=nodes, edges=edges)


# ──────────────────────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────────────────────


@router.get("/topics", response_model=GraphResponse)
async def graph_all_topics(
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> GraphResponse:
    """모든 topic + 그 topic 들에 속한 item 노드 + 엣지.

    graph UI 의 메인 view — 시작 화면. limit=100 (topic 기준) — 그 안의
    item 모두 펼침. 1000+ topic 환경에선 limit 조정.
    """
    topics = await list_topics(session, limit=limit)
    if not topics:
        return GraphResponse(nodes=[], edges=[])

    topic_ids = [t["id"] for t in topics]
    links = await list_item_topic_links(session, topic_ids=topic_ids)

    # 엣지의 unique item_id 추출 → 한 번에 노드 정보 fetch (N+1 회피)
    item_ids = list({lk["item_id"] for lk in links})
    items = await list_items_summary(session, item_ids=item_ids)

    return build_graph_response(topics, items, links)


@router.get("/search", response_model=GraphResponse)
async def graph_search(
    q: str = Query(..., min_length=1),
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> GraphResponse:
    """검색 (Postgres FTS) → graph subset.

    검색된 item + 그 item 들이 속한 topic + 두 종류의 노드 사이 엣지. graph UI
    의 검색 상자 입력 시 호출 — 결과 화면 전체가 검색 subset 으로 갱신.

    Qdrant 벡터 검색이 아닌 FTS — 빠르고 가볍게 graph subset 만 보여주는 게
    목적. 정밀 의미 검색은 /search 따로.
    """
    item_ids_uuid = await search_items_by_text(session, query=q, limit=limit)
    if not item_ids_uuid:
        return GraphResponse(nodes=[], edges=[])

    items = await list_items_summary(session, item_ids=item_ids_uuid)
    links = await list_item_topic_links(session, item_ids=item_ids_uuid)

    # 결과 item 의 모든 topic 도 노드로 표시 → 사용자가 어떤 cluster 인지 한 눈에
    topic_ids = list({lk["topic_id"] for lk in links})
    topics = []
    if topic_ids:
        all_topics = await list_topics(session, limit=500)
        topic_map = {t["id"]: t for t in all_topics}
        topics = [topic_map[tid] for tid in topic_ids if tid in topic_map]

    return build_graph_response(topics, items, links)


@router.get("/item/{item_id}", response_model=GraphResponse)
async def graph_item_neighborhood(
    item_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> GraphResponse:
    """한 item 의 이웃 — 같은 topic 의 다른 modality item + topic 노드들.

    graph UI 에서 노드 클릭 시 호출 — focus item + 인접 노드 확장 표시.
    빈 응답 (topic 없음, 다른 modality 없음) 도 valid — UI 가 단일 노드만 표시.

    404 가 아니라 빈 GraphResponse 반환 — graph 가 단일 isolated 노드로 표시되어
    UI consistency 유지 (item 자체가 정말 없으면 별도 GET /items/{id} 가 404).
    """
    item = await get_item_full(session, item_id)
    if item is None:
        return GraphResponse(nodes=[], edges=[])

    # 1. 이 item 의 모든 topic
    topic_links_self = await list_topics_for_item(session, item_id=item_id)
    topic_ids = [t["id"] for t in topic_links_self]

    # 2. 그 topic 들의 다른 item 들
    neighbor_items_by_id: dict[UUID, dict[str, Any]] = {}
    all_links: list[dict[str, Any]] = []
    for tid in topic_ids:
        for it in await list_items_for_topic(session, topic_id=tid):
            neighbor_items_by_id[it["id"]] = it
        # 각 topic 의 모든 link (자기 + 다른 item)
        topic_links = await list_item_topic_links(session, topic_ids=[tid])
        all_links.extend(topic_links)

    # 자기 item 도 노드에 포함 (정확한 표시 위해 summary list 형태로 변환)
    self_summary_list = await list_items_summary(session, item_ids=[item_id])
    items_combined = list(neighbor_items_by_id.values()) + self_summary_list

    # topic 노드 변환을 위해 list_topics 의 full row 필요 (item_count 등)
    topics_full: list[dict[str, Any]] = []
    if topic_ids:
        all_topics = await list_topics(session, limit=500)
        topic_map = {t["id"]: t for t in all_topics}
        topics_full = [topic_map[tid] for tid in topic_ids if tid in topic_map]

    return build_graph_response(topics_full, items_combined, all_links)
