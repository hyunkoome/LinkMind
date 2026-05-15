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


async def update_item_metadata(
    session: AsyncSession,
    *,
    item_id: UUID,
    title: str | None = None,
    source_metadata: dict[str, Any] | None = None,
) -> None:
    """title / source_metadata 갱신 — `force` 재ingest 시 새 fetch 결과 반영용.

    raw_content / raw_content_hash 는 절대 건드리지 않음 (loss-less 원칙).
    None 인 인자는 변경하지 않음 (COALESCE).
    """
    if title is None and source_metadata is None:
        return
    await session.execute(
        text("""
            UPDATE items SET
                title = COALESCE(:title, title),
                source_metadata = COALESCE(CAST(:meta AS JSONB), source_metadata)
            WHERE id = :id
        """),
        {
            "id": item_id,
            "title": title,
            "meta": _to_json(source_metadata) if source_metadata is not None else None,
        },
    )


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


async def list_items_by_tags(
    session: AsyncSession, *, tags: list[str], top_k: int,
) -> list[dict[str, Any]]:
    """tags 중 하나라도 매칭되는 items 를 최신순으로. `#tag` 만 있는 검색용."""
    if not tags:
        return []
    res = await session.execute(
        text("""
            SELECT id, source_type, source_url, title, summary, categories, tags,
                   ingested_at
            FROM items
            WHERE tags && CAST(:tags AS TEXT[])
            ORDER BY ingested_at DESC
            LIMIT :limit
        """),
        {"tags": tags, "limit": top_k},
    )
    return [dict(r) for r in res.mappings().all()]


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
# attachments
# ──────────────────────────────────────────────────────────────


async def insert_attachment(
    session: AsyncSession,
    *,
    item_id: UUID,
    file_path: str,
    file_hash: str,
    file_size: int,
    mime_type: str,
    role: str,
    width: int | None = None,
    height: int | None = None,
    caption: str | None = None,
) -> UUID | None:
    """일반화된 attachment INSERT — PDF 본체 / figure / YouTube thumbnail 등 공통.

    `ON CONFLICT (item_id, file_hash) DO NOTHING` — 동일 item 에 같은 파일을 여러 번
    insert 해도 안전 (force 재처리, backfill 등). 충돌 시 None 반환.
    """
    res = await session.execute(
        text("""
            INSERT INTO attachments (
                item_id, file_path, mime_type, file_size, file_hash,
                role, width, height, caption
            ) VALUES (
                :item_id, :file_path, :mime, :size, :hash,
                :role, :w, :h, :caption
            )
            ON CONFLICT (item_id, file_hash) DO NOTHING
            RETURNING id
        """),
        {
            "item_id": item_id,
            "file_path": file_path,
            "mime": mime_type,
            "size": file_size,
            "hash": file_hash,
            "role": role,
            "w": width,
            "h": height,
            "caption": caption,
        },
    )
    return res.scalar_one_or_none()


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
# app_settings  (런타임 key-value 설정)
# ──────────────────────────────────────────────────────────────


async def get_app_setting(session: AsyncSession, key: str) -> str | None:
    res = await session.execute(
        text("SELECT value FROM app_settings WHERE key = :k"),
        {"k": key},
    )
    return res.scalar_one_or_none()


async def get_all_app_settings(session: AsyncSession) -> dict[str, str]:
    res = await session.execute(text("SELECT key, value FROM app_settings"))
    return {r[0]: r[1] for r in res.all()}


async def set_app_setting(session: AsyncSession, key: str, value: str) -> None:
    await session.execute(
        text("""
            INSERT INTO app_settings (key, value)
            VALUES (:k, :v)
            ON CONFLICT (key) DO UPDATE
                SET value = EXCLUDED.value,
                    updated_at = now()
        """),
        {"k": key, "v": value},
    )


async def delete_app_setting(session: AsyncSession, key: str) -> None:
    await session.execute(text("DELETE FROM app_settings WHERE key = :k"), {"k": key})


# ──────────────────────────────────────────────────────────────
# prompts  (system prompt 버전 히스토리)
# ──────────────────────────────────────────────────────────────


async def get_active_prompt(
    session: AsyncSession, name: str
) -> dict[str, Any] | None:
    """name 의 활성 프롬프트 반환. {id, version, content, created_at} 또는 None."""
    res = await session.execute(
        text("""
            SELECT id, version, content, created_at
            FROM prompts
            WHERE name = :name AND is_active
        """),
        {"name": name},
    )
    row = res.mappings().one_or_none()
    return dict(row) if row else None


async def list_prompt_versions(
    session: AsyncSession, name: str
) -> list[dict[str, Any]]:
    res = await session.execute(
        text("""
            SELECT id, version, content, is_active, note, created_at
            FROM prompts
            WHERE name = :name
            ORDER BY created_at DESC
        """),
        {"name": name},
    )
    return [dict(r) for r in res.mappings().all()]


async def _next_version_label(session: AsyncSession, name: str) -> str:
    """name 안에서 다음 버전 라벨 — 기존 'vN' 들의 max N + 1. 없으면 v1."""
    res = await session.execute(
        text("""
            SELECT version FROM prompts
            WHERE name = :name AND version ~ '^v[0-9]+$'
        """),
        {"name": name},
    )
    nums = [int(v[1:]) for (v,) in res.all()]
    return f"v{(max(nums) if nums else 0) + 1}"


async def save_new_prompt_version(
    session: AsyncSession,
    *,
    name: str,
    content: str,
    note: str | None = None,
    activate: bool = True,
) -> dict[str, Any]:
    """새 버전 저장. activate=True 면 기존 활성 해제 후 이 버전을 활성으로."""
    version = await _next_version_label(session, name)
    if activate:
        await session.execute(
            text("UPDATE prompts SET is_active = FALSE WHERE name = :name AND is_active"),
            {"name": name},
        )
    res = await session.execute(
        text("""
            INSERT INTO prompts (name, version, content, is_active, note)
            VALUES (:name, :version, :content, :active, :note)
            RETURNING id, version, content, is_active, created_at
        """),
        {
            "name": name,
            "version": version,
            "content": content,
            "active": activate,
            "note": note,
        },
    )
    return dict(res.mappings().one())


async def activate_prompt_version(
    session: AsyncSession, *, name: str, version: str
) -> None:
    await session.execute(
        text("UPDATE prompts SET is_active = FALSE WHERE name = :name AND is_active"),
        {"name": name},
    )
    await session.execute(
        text("UPDATE prompts SET is_active = TRUE WHERE name = :name AND version = :version"),
        {"name": name, "version": version},
    )


async def ensure_seed_prompt(
    session: AsyncSession, *, name: str, default_content: str
) -> None:
    """name 에 대해 활성 prompt 가 하나도 없으면 default_content 로 v1 시드.
    이미 있으면 무시."""
    existing = await get_active_prompt(session, name)
    if existing is not None:
        return
    # 활성 row 가 없어도 history 가 있을 수 있으니, 둘 다 없을 때만 v1.
    res = await session.execute(
        text("SELECT 1 FROM prompts WHERE name = :name LIMIT 1"),
        {"name": name},
    )
    if res.first() is not None:
        # history 는 있는데 아무것도 활성 아님 → 최신 버전 활성화.
        latest = await session.execute(
            text("""
                SELECT version FROM prompts WHERE name = :name
                ORDER BY created_at DESC LIMIT 1
            """),
            {"name": name},
        )
        v = latest.scalar_one()
        await activate_prompt_version(session, name=name, version=v)
        return
    # 완전 새로 시작 — v1 시드 + 활성.
    await save_new_prompt_version(
        session,
        name=name,
        content=default_content,
        note="initial seed from code default",
        activate=True,
    )


# ──────────────────────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────────────────────


def _to_json(d: dict[str, Any]) -> str:
    """psycopg/asyncpg에 JSONB로 전달하기 위한 직렬화."""
    import orjson
    return orjson.dumps(d).decode("utf-8")
