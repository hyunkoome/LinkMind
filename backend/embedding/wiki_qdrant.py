"""
Qdrant wiki_pages 컬렉션 — D10 wave-1f (2026-05-26).

chunks 컬렉션 (linkmind_items) 과 분리된 별 컬렉션 (linkmind_wiki_pages).
이유:
  - wiki body 는 chunk 보다 큼 (수천~수만 자) — 한 page = 1 point 가 자연
  - 검색 시 chunk top-k 와 무관 (wiki 단위 의미 검색)
  - body 단위 score 가 chunk garbage 영향 X (§0.1 검색 fix 의 핵심)

각 wiki_page = 1 point. point.id = wiki_pages.id (UUID).
payload: slug, title, description, source_count, body_status, is_pinned.
vector: bge-m3 가 합성한 body 의 embedding (dim=1024).
"""

from __future__ import annotations

import logging
from typing import Any

from qdrant_client.http import models as qmodels

from backend.embedding.qdrant_store import get_qdrant_client

logger = logging.getLogger("linkmind.embedding.wiki_qdrant")


WIKI_COLLECTION = "linkmind_wiki_pages"


async def ensure_wiki_collection(dim: int) -> None:
    """wiki_pages 컬렉션 생성 (없으면). 있으면 dim 검증만."""
    client = get_qdrant_client()
    existing = await client.get_collections()
    names = {c.name for c in existing.collections}
    if WIKI_COLLECTION in names:
        info = await client.get_collection(WIKI_COLLECTION)
        actual_dim = info.config.params.vectors.size  # type: ignore[union-attr]
        if actual_dim != dim:
            raise RuntimeError(
                f"Qdrant 컬렉션 '{WIKI_COLLECTION}' dim={actual_dim} != provider dim={dim}. "
                f"모델 변경 시 컬렉션 재생성 필요."
            )
        return

    logger.info("Qdrant 컬렉션 생성: %s (dim=%d)", WIKI_COLLECTION, dim)
    await client.create_collection(
        collection_name=WIKI_COLLECTION,
        vectors_config=qmodels.VectorParams(size=dim, distance=qmodels.Distance.COSINE),
    )
    # 자주 쓰는 payload 인덱스
    for field, schema in [
        ("slug", qmodels.PayloadSchemaType.KEYWORD),
        ("body_status", qmodels.PayloadSchemaType.KEYWORD),
        ("is_pinned", qmodels.PayloadSchemaType.BOOL),
    ]:
        await client.create_payload_index(
            collection_name=WIKI_COLLECTION,
            field_name=field,
            field_schema=schema,
        )


async def upsert_wiki_page(
    *,
    page_id: str,
    vector: list[float],
    payload: dict[str, Any],
) -> None:
    """1 wiki_page = 1 point. page_id (UUID) 그대로 point.id 사용."""
    client = get_qdrant_client()
    await client.upsert(
        collection_name=WIKI_COLLECTION,
        points=[qmodels.PointStruct(id=page_id, vector=vector, payload=payload)],
        wait=True,
    )


async def delete_wiki_page(page_id: str) -> int:
    """wiki_pages CASCADE 시 Qdrant point 도 정리 (frontend 의 영구 삭제 흐름)."""
    client = get_qdrant_client()
    result = await client.delete(
        collection_name=WIKI_COLLECTION,
        points_selector=qmodels.PointIdsList(points=[page_id]),
        wait=True,
    )
    return 0 if str(result.status).endswith("completed") else -1


async def search_wiki_pages(
    *,
    query_vector: list[float],
    top_k: int,
    pinned_only: bool = False,
    status_filter: list[str] | None = None,
) -> list[qmodels.ScoredPoint]:
    """wiki body embedding 기반 의미 검색 — chunk top-k 와 분리."""
    client = get_qdrant_client()
    must: list[qmodels.FieldCondition] = []
    if pinned_only:
        must.append(qmodels.FieldCondition(
            key="is_pinned", match=qmodels.MatchValue(value=True),
        ))
    if status_filter:
        must.append(qmodels.FieldCondition(
            key="body_status", match=qmodels.MatchAny(any=status_filter),
        ))
    query_filter = qmodels.Filter(must=must) if must else None

    result = await client.query_points(
        collection_name=WIKI_COLLECTION,
        query=query_vector,
        limit=top_k,
        query_filter=query_filter,
        with_payload=True,
    )
    return result.points
