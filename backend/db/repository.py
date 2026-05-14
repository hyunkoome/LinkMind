"""
Postgres 데이터 액세스 — items / chunks / attachments.

SQLAlchemy Core text() 기반. ORM 모델을 만들지 않은 이유는 MVP 단계에서 schema.sql이
단일 진실 소스(single source of truth)이고, 컬럼 추가 시 한 곳만 고치면 되기 때문.
Phase 2에 Alembic + ORM 도입 시 점진적으로 전환.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


# ──────────────────────────────────────────────────────────────
# items
# ──────────────────────────────────────────────────────────────


async def find_item_by_hash(
    session: AsyncSession,
    *,
    source_type: str,
    content_hash: str,
) -> UUID | None:
    row = await session.execute(
        text("SELECT id FROM items WHERE source_type = :st AND raw_content_hash = :h"),
        {"st": source_type, "h": content_hash},
    )
    val = row.scalar_one_or_none()
    return val  # type: ignore[return-value]


async def insert_item(
    session: AsyncSession,
    *,
    source_type: str,
    raw_content: str,
    raw_content_hash: str,
    source_id: str | None,
    source_url: str | None,
    source_metadata: dict[str, Any] | None,
    title: str | None,
    source_created_at: datetime | None,
) -> UUID:
    row = await session.execute(
        text("""
            INSERT INTO items (
                source_type, source_id, source_url, source_metadata,
                raw_content, raw_content_hash, title, source_created_at
            ) VALUES (
                :source_type, :source_id, :source_url, CAST(:source_metadata AS JSONB),
                :raw_content, :raw_content_hash, :title, :source_created_at
            )
            RETURNING id
        """),
        {
            "source_type": source_type,
            "source_id": source_id,
            "source_url": str(source_url) if source_url else None,
            "source_metadata": _to_json(source_metadata or {}),
            "raw_content": raw_content,
            "raw_content_hash": raw_content_hash,
            "title": title,
            "source_created_at": source_created_at,
        },
    )
    return row.scalar_one()


async def update_item_analysis(
    session: AsyncSession,
    *,
    item_id: UUID,
    summary: str | None,
    summary_model: str | None,
    summary_prompt_version: str | None,
    categories: list[str] | None,
    tags: list[str] | None,
) -> None:
    await session.execute(
        text("""
            UPDATE items SET
                summary = COALESCE(:summary, summary),
                summary_model = COALESCE(:summary_model, summary_model),
                summary_prompt_version = COALESCE(:spv, summary_prompt_version),
                summary_generated_at = CASE WHEN :summary IS NOT NULL THEN now() ELSE summary_generated_at END,
                categories = COALESCE(:categories, categories),
                tags = COALESCE(:tags, tags)
            WHERE id = :id
        """),
        {
            "id": item_id,
            "summary": summary,
            "summary_model": summary_model,
            "spv": summary_prompt_version,
            "categories": categories,
            "tags": tags,
        },
    )


async def get_items_by_ids(session: AsyncSession, ids: list[UUID]) -> dict[UUID, dict[str, Any]]:
    if not ids:
        return {}
    res = await session.execute(
        text("""
            SELECT id, source_type, source_url, title, summary, categories, tags
            FROM items
            WHERE id = ANY(:ids)
        """),
        {"ids": list(ids)},
    )
    rows = res.mappings().all()
    return {r["id"]: dict(r) for r in rows}


# ──────────────────────────────────────────────────────────────
# chunks
# ──────────────────────────────────────────────────────────────


async def insert_chunks(
    session: AsyncSession,
    *,
    item_id: UUID,
    chunks: list[str],
    embedding_model: str,
    embedding_dim: int,
) -> list[UUID]:
    """chunks를 일괄 INSERT, RETURNING으로 id 반환."""
    if not chunks:
        return []
    # asyncpg는 executemany RETURNING이 약하므로 단건 INSERT 루프가 안정적.
    # 대용량 시 COPY 또는 unnest 방식으로 최적화 가능 (Phase 2).
    ids: list[UUID] = []
    for idx, ctext in enumerate(chunks):
        row = await session.execute(
            text("""
                INSERT INTO chunks (item_id, chunk_index, chunk_text, embedding_model, embedding_dim)
                VALUES (:item_id, :idx, :ctext, :em, :ed)
                RETURNING id
            """),
            {
                "item_id": item_id,
                "idx": idx,
                "ctext": ctext,
                "em": embedding_model,
                "ed": embedding_dim,
            },
        )
        ids.append(row.scalar_one())
    return ids


# ──────────────────────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────────────────────


def _to_json(d: dict[str, Any]) -> str:
    """psycopg/asyncpg에 JSONB로 전달하기 위한 직렬화."""
    import orjson
    return orjson.dumps(d).decode("utf-8")
