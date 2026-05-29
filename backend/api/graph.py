"""
GET /graph/keywords, /graph/keyword/{kw}, /graph/wiki/{slug}, /graph/item/{id}, /graph/search

cytoscape.js 호환 JSON ({nodes, edges}) 반환. frontend graph UI 가 이 endpoint 만 호출.

D10.5 세션 B (2026-05-29) — 그룹 축을 **wiki keyword** 로 통일 (옛 category/topic 폐기).
계층: keyword ▸ wiki ▸ item.
- keyword: wiki_pages.keywords (정규화 영문, D10.6) 의 distinct 값. 그룹 노드.
- wiki: wiki_pages. 자료의 합성 페이지 (1링크=1위키).
- item: items. 실 자료.
엣지: keyword↔wiki (wiki.keywords @> [kw]), wiki↔item (wiki_page_items).
raw_content 같은 큰 필드는 노드에 X — 클릭 시 GET /items/{id} / GET /wiki/{slug} 별 fetch.
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
    list_cooccurring_keywords,
    list_items_summary,
    list_keyword_counts,
    list_wiki_item_links,
    list_wiki_nodes,
    list_wikis_for_keyword,
    search_items_by_text,
)

# keyword 클릭 시 그 키워드의 위키 노드 수 상한 (co-occurrence 그래프 가독성).
COOCCUR_WIKI_LIMIT = 30
from backend.schemas.models import GraphEdge, GraphNode, GraphResponse

logger = logging.getLogger(__name__)
router = APIRouter()


# ──────────────────────────────────────────────────────────────
# cytoscape 변환 helper (pure 함수 — tests/ 에서 직접 unit)
# ──────────────────────────────────────────────────────────────


def _short_summary(summary: str | None, *, max_chars: int = 200) -> str | None:
    if not summary:
        return None
    s = summary.strip()
    if len(s) <= max_chars:
        return s
    return s[:max_chars].rstrip() + "…"


def keyword_to_node(keyword: str, wiki_count: int) -> GraphNode:
    """keyword 그룹 노드. id 는 'keyword:<kw>'. (옛 category 자리)"""
    return GraphNode(
        data={
            "id": f"keyword:{keyword}",
            "label": keyword,
            "type": "keyword",
            "slug": keyword,
            "wiki_count": int(wiki_count or 0),
        }
    )


def wiki_to_node(wiki: dict[str, Any]) -> GraphNode:
    """wiki 노드. id 는 'wiki:<slug>'. (옛 topic 자리)

    색은 frontend 가 primary_external_id 로 결정 (topicKindColor 재사용).
    """
    return GraphNode(
        data={
            "id": f"wiki:{wiki['slug']}",
            "label": wiki.get("title") or wiki.get("slug") or "(untitled)",
            "type": "wiki",
            "slug": wiki.get("slug"),
            "title": wiki.get("title"),
            "item_count": int(wiki.get("item_count") or 0),
            "keywords": list(wiki.get("keywords") or []),
            "primary_external_id": wiki.get("primary_external_id") or {},
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


def keyword_wiki_edge(keyword: str, wiki_slug: str) -> GraphEdge:
    """keyword → wiki 엣지. id 'edge:kw:<kw>:<wiki_slug>'."""
    return GraphEdge(
        data={
            "id": f"edge:kw:{keyword}:{wiki_slug}",
            "source": f"keyword:{keyword}",
            "target": f"wiki:{wiki_slug}",
            "role": "keyword",
            "confidence": 1.0,
            "link_source": "auto",
        }
    )


def keyword_keyword_edge(kw1: str, kw2: str, shared: int) -> GraphEdge:
    """keyword ↔ keyword co-occurrence 엣지 (같은 위키 공유). id 정렬로 dedup."""
    a, b = sorted([kw1, kw2])
    return GraphEdge(
        data={
            "id": f"edge:kwkw:{a}:{b}",
            "source": f"keyword:{kw1}",
            "target": f"keyword:{kw2}",
            "role": "cooccur",
            "confidence": float(shared),
            "link_source": "auto",
        }
    )


def wiki_item_edge(wiki_slug: str, item_id: Any, link: dict[str, Any]) -> GraphEdge:
    """wiki → item 엣지 (wiki_page_items). id 'edge:wiki:<wiki_slug>:<item>'."""
    return GraphEdge(
        data={
            "id": f"edge:wiki:{wiki_slug}:{item_id}",
            "source": f"wiki:{wiki_slug}",
            "target": f"item:{item_id}",
            "role": link.get("role") or "source",
            "confidence": float(link.get("confidence") or 1.0),
            "link_source": link.get("source") or "auto",
        }
    )


def _build_kwi(
    keyword_node: GraphNode | None,
    wikis: list[dict[str, Any]],
    items: list[dict[str, Any]],
    links: list[dict[str, Any]],
    wiki_by_id: dict[Any, dict[str, Any]],
) -> GraphResponse:
    """keyword(옵션) + wiki + item 노드 + (keyword↔wiki, wiki↔item) 엣지 조립.

    id 중복 자동 제거 — 같은 노드가 여러 path 로 와도 안전. dangling edge 방지를 위해
    wiki_by_id 에 없는 link 는 skip (frontend force-graph 'node not found' 회피).
    """
    nodes: list[GraphNode] = []
    seen: set[str] = set()

    def add(n: GraphNode) -> None:
        if n.data["id"] not in seen:
            seen.add(n.data["id"])
            nodes.append(n)

    if keyword_node is not None:
        add(keyword_node)
    for w in wikis:
        add(wiki_to_node(w))
    for it in items:
        add(item_to_node(it))

    edges: list[GraphEdge] = []
    seen_e: set[str] = set()

    def add_e(e: GraphEdge) -> None:
        if e.data["id"] not in seen_e:
            seen_e.add(e.data["id"])
            edges.append(e)

    if keyword_node is not None:
        kw = keyword_node.data["slug"]
        for w in wikis:
            add_e(keyword_wiki_edge(kw, w["slug"]))
    item_node_ids = {n.data["id"] for n in nodes if n.data["type"] == "item"}
    for lk in links:
        w = wiki_by_id.get(lk["wiki_page_id"])
        if not w:
            continue
        # item 노드가 실제로 포함된 경우만 엣지 (dangling 방지)
        if f"item:{lk['item_id']}" not in item_node_ids:
            continue
        add_e(wiki_item_edge(w["slug"], lk["item_id"], lk))

    return GraphResponse(nodes=nodes, edges=edges)


# ──────────────────────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────────────────────


@router.get("/keywords", response_model=GraphResponse)
async def graph_keywords(
    limit: int = Query(default=100000, ge=1, le=100000),
    session: AsyncSession = Depends(get_session),
) -> GraphResponse:
    """keyword 그룹 노드 (빈도순) — 메인 진입 view. 기본 전부 (사용자 요구).

    keyword 노드만 가볍게 (wiki/item 은 클릭 시 GET /graph/keyword/{kw} 로 expand).
    """
    kws = await list_keyword_counts(session, limit=limit)
    nodes = [keyword_to_node(k["keyword"], k["usage_count"]) for k in kws]
    return GraphResponse(nodes=nodes, edges=[])


@router.get("/keyword/{keyword}", response_model=GraphResponse)
async def graph_keyword_expand(
    keyword: str,
    session: AsyncSession = Depends(get_session),
) -> GraphResponse:
    """keyword 클릭 → 지역 co-occurrence 클러스터 (동적, 실시간).

    중심 keyword + 같은 위키를 공유하는 다른 키워드(co-occur) + 그 keyword 의
    위키들. 엣지: keyword↔keyword (cooccur) + keyword→wiki. 새 자료가 들어오면
    다음 호출에 자동 반영 (전역 배치 그룹화 X — 클릭 중심 동적).
    """
    cooccur = await list_cooccurring_keywords(session, keyword=keyword)
    wikis = await list_wikis_for_keyword(session, keyword=keyword, limit=COOCCUR_WIKI_LIMIT)

    nodes: list[GraphNode] = []
    seen: set[str] = set()

    def add(n: GraphNode) -> None:
        if n.data["id"] not in seen:
            seen.add(n.data["id"])
            nodes.append(n)

    add(keyword_to_node(keyword, len(wikis)))
    for c in cooccur:
        add(keyword_to_node(c["keyword"], int(c["shared"])))
    for w in wikis:
        add(wiki_to_node(w))

    edges: list[GraphEdge] = []
    seen_e: set[str] = set()

    def add_e(e: GraphEdge) -> None:
        if e.data["id"] not in seen_e:
            seen_e.add(e.data["id"])
            edges.append(e)

    for c in cooccur:
        add_e(keyword_keyword_edge(keyword, c["keyword"], int(c["shared"])))
    for w in wikis:
        add_e(keyword_wiki_edge(keyword, w["slug"]))
    return GraphResponse(nodes=nodes, edges=edges)


@router.get("/wiki/{slug}", response_model=GraphResponse)
async def graph_wiki_expand(
    slug: str,
    session: AsyncSession = Depends(get_session),
) -> GraphResponse:
    """wiki 클릭 시 expand — 그 wiki 1개 + 그 안의 item 들 (sources/figures).

    노드: wiki 1 + item N. 엣지: wiki→item.
    """
    wikis = await list_wiki_nodes(session, slugs=[slug])
    if not wikis:
        return GraphResponse(nodes=[], edges=[])

    wiki_by_id = {w["id"]: w for w in wikis}
    links = await list_wiki_item_links(session, wiki_page_ids=[wikis[0]["id"]])
    item_ids = list({lk["item_id"] for lk in links})
    items = await list_items_summary(session, item_ids=item_ids)
    return _build_kwi(None, wikis, items, links, wiki_by_id)


@router.get("/item/{item_id}", response_model=GraphResponse)
async def graph_item_neighborhood(
    item_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> GraphResponse:
    """한 item 의 이웃 — 그 item 이 속한 wiki(들) + 형제 item.

    graph UI 에서 자료 노드 클릭 시 호출 — 그 자료의 wiki 와 같은 wiki 의 다른
    자료들을 보여줌. wiki 가 없으면 item 단독 노드 (404 대신 빈/단일).
    """
    item = await get_item_full(session, item_id)
    if item is None:
        return GraphResponse(nodes=[], edges=[])

    wiki_refs = item.get("wikis") or []
    slugs = [w["slug"] for w in wiki_refs if w.get("slug")]
    if not slugs:
        # wiki 미분류 자료 — 단독 노드
        items = await list_items_summary(session, item_ids=[item_id])
        return GraphResponse(
            nodes=[item_to_node(it) for it in items], edges=[]
        )

    wikis = await list_wiki_nodes(session, slugs=slugs)
    wiki_by_id = {w["id"]: w for w in wikis}
    links = await list_wiki_item_links(
        session, wiki_page_ids=[w["id"] for w in wikis]
    )
    item_ids = list({lk["item_id"] for lk in links} | {item_id})
    items = await list_items_summary(session, item_ids=item_ids)
    return _build_kwi(None, wikis, items, links, wiki_by_id)


@router.get("/search", response_model=GraphResponse)
async def graph_search(
    q: str = Query(..., min_length=1),
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> GraphResponse:
    """검색 (Postgres FTS) → graph subset.

    검색된 item + 그 item 들이 속한 wiki + 두 종류 노드 사이 엣지. 정밀 의미
    검색은 POST /wiki/search (Qdrant) 따로 — 여기는 가벼운 graph subset 용.
    """
    item_ids_uuid = await search_items_by_text(session, query=q, limit=limit)
    if not item_ids_uuid:
        return GraphResponse(nodes=[], edges=[])

    items = await list_items_summary(session, item_ids=item_ids_uuid)
    links = await list_wiki_item_links(session, item_ids=item_ids_uuid)
    wiki_ids = list({lk["wiki_page_id"] for lk in links})
    wikis = await list_wiki_nodes(session, ids=wiki_ids)
    wiki_by_id = {w["id"]: w for w in wikis}
    return _build_kwi(None, wikis, items, links, wiki_by_id)
