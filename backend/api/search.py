"""
POST /search — Semantic search (Qdrant) + Postgres metadata join.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.connection import get_session
from backend.db.repository import get_items_by_ids
from backend.embedding.factory import get_embedding_provider
from backend.embedding.qdrant_store import search_chunks
from backend.schemas.models import SearchHit, SearchRequest, SearchResponse

router = APIRouter()


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
        top_k=payload.top_k,
        source_types=payload.source_types,
        categories=payload.categories,
        tags=payload.tags,
    )

    # Qdrant payload에서 item_id 모아서 한 번에 Postgres join (N+1 방지)
    item_ids: list[UUID] = []
    for p in points:
        if p.payload and "item_id" in p.payload:
            item_ids.append(UUID(p.payload["item_id"]))
    items = await get_items_by_ids(session, item_ids)

    hits: list[SearchHit] = []
    for p in points:
        if not p.payload:
            continue
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
