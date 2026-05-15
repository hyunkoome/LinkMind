"""
POST /search — Semantic search (Qdrant) + Postgres metadata join.

검색은 chunk 단위로 매칭되지만, 결과 list 는 item(문서) 단위로 dedup 한다.
같은 문서의 여러 chunk 가 상위에 몰릴 때 결과가 똑같은 행으로 도배되지 않도록.
각 item 에서는 score 가 가장 높은 chunk 의 snippet 만 대표로 보여준다.

query 에 `#hashtag` 토큰이 있으면 자동으로 tag 필터로 추출 — 토큰 자체는 임베딩
쿼리에서 제거. `#SLAM 3DGS` 같은 혼합도, `#SLAM #3DGS` 같이 태그만도 가능.
태그만 있는 경우 (남은 텍스트가 비면) Qdrant 호출 대신 Postgres `items.tags`
GIN 인덱스로 바로 최신순 조회.
"""

from __future__ import annotations

import re
from uuid import UUID

from fastapi import APIRouter, Depends
from qdrant_client.http.models import ScoredPoint
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.connection import get_session
from backend.db.repository import get_items_by_ids, list_items_by_tags
from backend.embedding.factory import get_embedding_provider
from backend.embedding.qdrant_store import search_chunks
from backend.schemas.models import SearchHit, SearchRequest, SearchResponse

router = APIRouter()

_OVERFETCH_FACTOR = 5
# # 다음 영문/숫자/한글/하이픈/언더스코어/점 — ingest 의 hashtag 규칙과 동일.
_HASHTAG_RE = re.compile(r"#([A-Za-z0-9가-힣_\-\.]+)")


def _split_hashtags(query: str) -> tuple[str, list[str]]:
    """query 에서 `#tag` 토큰들을 분리. (remaining_text, tags) 반환.
    tags 는 `#` 제거된 raw 형태 — 매칭은 case-sensitive (저장된 form 그대로)."""
    tags = _HASHTAG_RE.findall(query)
    remaining = _HASHTAG_RE.sub(" ", query)
    remaining = re.sub(r"\s+", " ", remaining).strip()
    return remaining, tags


@router.post("", response_model=SearchResponse)
async def search(
    payload: SearchRequest,
    session: AsyncSession = Depends(get_session),
) -> SearchResponse:
    text_query, hashtag_tags = _split_hashtags(payload.query)
    all_tags = [*(payload.tags or []), *hashtag_tags]

    # 분기: 텍스트는 비고 #tag 만 있는 검색 → Postgres 만으로 최신순 반환.
    if not text_query and all_tags:
        rows = await list_items_by_tags(session, tags=all_tags, top_k=payload.top_k)
        hits = [
            SearchHit(
                item_id=r["id"],
                chunk_id=None,
                score=1.0,                                # tag 매칭은 score 의미 없음
                title=r.get("title"),
                summary=r.get("summary"),
                snippet=None,
                source_type=r["source_type"],
                source_url=r.get("source_url"),
                categories=r.get("categories") or [],
                tags=r.get("tags") or [],
            )
            for r in rows
        ]
        return SearchResponse(query=payload.query, hits=hits)

    # 일반 흐름: Qdrant 벡터 검색 (텍스트가 비면 원래 query 그대로 — 사용자가 빈 검색을
    # 시도하면 어쨌든 빈 임베딩이 아니라 placeholder 가 들어가도록).
    embed_q = text_query or payload.query
    embedder = get_embedding_provider()
    emb = await embedder.embed([embed_q])
    qv = emb.vectors[0]

    points = await search_chunks(
        query_vector=qv,
        top_k=payload.top_k * _OVERFETCH_FACTOR,
        source_types=payload.source_types,
        categories=payload.categories,
        tags=all_tags or None,
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
