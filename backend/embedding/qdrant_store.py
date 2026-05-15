"""
Qdrant 컬렉션 관리 + 벡터 upsert/search.

컬렉션 1개를 공유 (linkmind_items). chunk.id를 그대로 point.id로 사용.
payload에 item_id, source_type, categories, tags 등 검색 필터링용 메타를 둠.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qmodels

from backend.config import get_settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_qdrant_client() -> AsyncQdrantClient:
    settings = get_settings()
    return AsyncQdrantClient(url=settings.effective_qdrant_url)


async def ensure_collection(dim: int) -> None:
    """컬렉션이 없으면 생성. 이미 있으면 dim 검증만."""
    settings = get_settings()
    client = get_qdrant_client()
    name = settings.qdrant_collection

    existing = await client.get_collections()
    names = {c.name for c in existing.collections}
    if name in names:
        info = await client.get_collection(name)
        if info.config.params.vectors.size != dim:  # type: ignore[union-attr]
            raise RuntimeError(
                f"Qdrant 컬렉션 '{name}'의 dim={info.config.params.vectors.size}이지만 "  # type: ignore[union-attr]
                f"EmbeddingProvider dim={dim}. 모델 변경 시 컬렉션을 재생성하세요."
            )
        return

    logger.info("Qdrant 컬렉션 생성: %s (dim=%d)", name, dim)
    await client.create_collection(
        collection_name=name,
        vectors_config=qmodels.VectorParams(size=dim, distance=qmodels.Distance.COSINE),
    )
    # 필터링이 자주 일어나는 payload 키에 인덱스 생성
    for field, schema in [
        ("item_id", qmodels.PayloadSchemaType.KEYWORD),
        ("source_type", qmodels.PayloadSchemaType.KEYWORD),
        ("categories", qmodels.PayloadSchemaType.KEYWORD),
        ("tags", qmodels.PayloadSchemaType.KEYWORD),
    ]:
        await client.create_payload_index(
            collection_name=name,
            field_name=field,
            field_schema=schema,
        )


async def upsert_chunks(
    *,
    chunk_ids: list[str],
    vectors: list[list[float]],
    payloads: list[dict[str, Any]],
) -> None:
    settings = get_settings()
    client = get_qdrant_client()
    points = [
        qmodels.PointStruct(id=cid, vector=vec, payload=pl)
        for cid, vec, pl in zip(chunk_ids, vectors, payloads, strict=True)
    ]
    await client.upsert(collection_name=settings.qdrant_collection, points=points, wait=True)


async def set_payload_for_item_chunks(
    *, item_id: str, payload: dict[str, Any],
) -> None:
    """item_id 매칭 모든 chunk 의 payload 에 부분 갱신 (set, replace 아님).

    Ingest 흐름상 chunks 는 summary 보다 먼저 upsert 되므로, tags 가 정해진 후
    이 함수로 chunk payload 의 tags 를 채워준다. Qdrant 의 tag 필터링이 가능해짐.
    """
    settings = get_settings()
    client = get_qdrant_client()
    await client.set_payload(
        collection_name=settings.qdrant_collection,
        payload=payload,
        points=qmodels.Filter(
            must=[qmodels.FieldCondition(
                key="item_id",
                match=qmodels.MatchValue(value=item_id),
            )]
        ),
        wait=True,
    )


async def search_chunks(
    *,
    query_vector: list[float],
    top_k: int,
    source_types: list[str] | None = None,
    categories: list[str] | None = None,
    tags: list[str] | None = None,
) -> list[qmodels.ScoredPoint]:
    settings = get_settings()
    client = get_qdrant_client()

    must: list[qmodels.FieldCondition] = []
    if source_types:
        must.append(qmodels.FieldCondition(
            key="source_type",
            match=qmodels.MatchAny(any=source_types),
        ))
    if categories:
        must.append(qmodels.FieldCondition(
            key="categories",
            match=qmodels.MatchAny(any=categories),
        ))
    if tags:
        must.append(qmodels.FieldCondition(
            key="tags",
            match=qmodels.MatchAny(any=tags),
        ))
    query_filter = qmodels.Filter(must=must) if must else None

    # qdrant-client v1.12+ 부터 .search() 가 제거됨 → .query_points() 로 대체.
    # 시그니처 차이: query_vector= → query=, 반환은 QueryResponse 라 .points 추출.
    result = await client.query_points(
        collection_name=settings.qdrant_collection,
        query=query_vector,
        limit=top_k,
        query_filter=query_filter,
        with_payload=True,
    )
    return result.points
