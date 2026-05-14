"""
POST /ingest — 외부 client(OpenClaw extension, 스크립트 등)가 자료를 LinkMind에 넣는 엔드포인트.

흐름:
  1) raw_content hash 계산
  2) 동일 hash 존재하면 skip (idempotent)
  3) items 에 INSERT
  4) (analyze_now=True) chunking → embedding → chunks INSERT → Qdrant upsert
  5) (옵션) AI 요약/태깅은 추후 background task로 분리 (Phase 2)
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.db.connection import get_session
from backend.db.repository import (
    find_item_by_hash,
    insert_chunks,
    insert_item,
)
from backend.embedding.factory import get_embedding_provider
from backend.embedding.qdrant_store import ensure_collection, upsert_chunks
from backend.schemas.models import IngestRequest, IngestResponse
from backend.utils.chunking import chunk_text
from backend.utils.hashing import sha256_text

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("", response_model=IngestResponse)
async def ingest(
    payload: IngestRequest,
    session: AsyncSession = Depends(get_session),
) -> IngestResponse:
    settings = get_settings()
    content_hash = sha256_text(payload.raw_content)

    # 1) idempotent 체크
    existing = await find_item_by_hash(
        session, source_type=payload.source_type, content_hash=content_hash
    )
    if existing is not None:
        return IngestResponse(item_id=existing, created=False, chunks_indexed=0)

    # 2) item 저장 (raw-first)
    item_id = await insert_item(
        session,
        source_type=payload.source_type,
        raw_content=payload.raw_content,
        raw_content_hash=content_hash,
        source_id=payload.source_id,
        source_url=str(payload.source_url) if payload.source_url else None,
        source_metadata=payload.source_metadata,
        title=payload.title,
        source_created_at=payload.source_created_at,
    )
    await session.commit()

    # 3) (옵션) 즉시 임베딩
    chunks_indexed = 0
    if payload.analyze_now:
        try:
            chunks_indexed = await _embed_and_index(
                session, item_id=item_id, raw_content=payload.raw_content,
                source_type=payload.source_type,
            )
        except Exception as e:                   # noqa: BLE001 — embedding 실패해도 raw는 보존됨
            logger.exception("embedding/index 실패 (item_id=%s): %s", item_id, e)
            # raw_content는 이미 저장됐으므로 나중에 재처리 가능.
            raise HTTPException(status_code=500, detail=f"embedding 실패: {e!s}") from e

    return IngestResponse(item_id=item_id, created=True, chunks_indexed=chunks_indexed)


async def _embed_and_index(
    session: AsyncSession,
    *,
    item_id: Any,
    raw_content: str,
    source_type: str,
) -> int:
    """chunking → embedding → DB chunks + Qdrant upsert."""
    embedder = get_embedding_provider()
    await ensure_collection(dim=embedder.dim)

    chunks = chunk_text(raw_content)
    if not chunks:
        return 0

    emb = await embedder.embed(chunks)
    chunk_ids = await insert_chunks(
        session,
        item_id=item_id,
        chunks=chunks,
        embedding_model=embedder.model,
        embedding_dim=embedder.dim,
    )
    await session.commit()

    # Qdrant payload (검색 필터링용 메타만 — 본문은 Postgres에서 join)
    payloads = [
        {
            "item_id": str(item_id),
            "chunk_index": idx,
            "source_type": source_type,
            "snippet": ctext[:300],
        }
        for idx, ctext in enumerate(chunks)
    ]
    await upsert_chunks(
        chunk_ids=[str(cid) for cid in chunk_ids],
        vectors=emb.vectors,
        payloads=payloads,
    )
    return len(chunks)
