"""
POST /search — Semantic search (Qdrant) + Postgres metadata join.

검색은 chunk 단위로 매칭되지만, 결과 list 는 item(문서) 단위로 dedup 한다.
같은 문서의 여러 chunk 가 상위에 몰릴 때 결과가 똑같은 행으로 도배되지 않도록.
각 item 에서는 score 가 가장 높은 chunk 의 snippet 만 대표로 보여준다.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from qdrant_client.http.models import ScoredPoint
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.connection import get_session
from backend.db.repository import get_items_by_ids
from backend.embedding.factory import get_embedding_provider
from backend.embedding.qdrant_store import search_chunks
from backend.schemas.models import SearchHit, SearchRequest, SearchResponse

router = APIRouter()

# 한 item 당 chunk 가 여러 개일 때 dedup 후에도 top_k 개를 채우려면 chunk 를 넉넉히
# 받아둬야 함. 5배면 평균 chunk/item 비율 (보통 3~10) 을 고려해도 거의 항상 채워짐.
_OVERFETCH_FACTOR = 5


@router.post("", response_model=SearchResponse)
async def search(
    payload: SearchRequest,
    session: AsyncSession = Depends(get_session),
) -> SearchResponse:
    embedder = get_embedding_provider()
    emb = await embedder.embed([payload.query])
    qv = emb.vectors[0]

    points = await search_chunks(
        query_vector=qv,
        top_k=payload.top_k * _OVERFETCH_FACTOR,
        source_types=payload.source_types,
        categories=payload.categories,
        tags=payload.tags,
    )

    # item_id 별로 score 최고인 chunk 한 개만 유지 → 결과는 item 단위.
    best_by_item: dict[UUID, ScoredPoint] = {}
    for p in points:
        if not p.payload or "item_id" not in p.payload:
            continue
        iid = UUID(p.payload["item_id"])
        if iid not in best_by_item or p.score > best_by_item[iid].score:
            best_by_item[iid] = p
    dedup_points = sorted(
        best_by_item.values(), key=lambda x: x.score, reverse=True,
    )[: payload.top_k]

    # 한 번에 Postgres join (N+1 방지)
    item_ids: list[UUID] = [UUID(p.payload["item_id"]) for p in dedup_points]
    items = await get_items_by_ids(session, item_ids)

    hits: list[SearchHit] = []
    for p in dedup_points:
        item_uuid = UUID(p.payload["item_id"])
        item = items.get(item_uuid, {})
        hits.append(SearchHit(
            item_id=item_uuid,
            chunk_id=UUID(str(p.id)) if p.id else None,
            score=float(p.score),
            title=item.get("title"),
            summary=item.get("summary"),
            snippet=p.payload.get("snippet"),
            source_type=item.get("source_type") or p.payload.get("source_type", "manual"),
            source_url=item.get("source_url"),
            categories=item.get("categories") or [],
            tags=item.get("tags") or [],
        ))

    return SearchResponse(query=payload.query, hits=hits)
