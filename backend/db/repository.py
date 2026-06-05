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


async def get_item_full(session: AsyncSession, item_id: UUID) -> dict[str, Any] | None:
    """GET /items/{id} — 모든 컬럼 + attachments 목록.

    raw_content 가 포함되어 크기 클 수 있음 (수십~수백 KB) — modality viewer
    전용. 일반 검색 결과엔 get_items_by_ids 의 slim subset 만 반환.
    """
    res = await session.execute(
        text("""
            SELECT
                id, source_type, source_id, source_url, source_metadata,
                title, summary, raw_content,
                categories, tags, language,
                source_created_at, ingested_at, updated_at,
                user_notes, user_notes_updated_at, is_read, read_at
            FROM items
            WHERE id = :id
        """),
        {"id": item_id},
    )
    row = res.mappings().one_or_none()
    if row is None:
        return None
    item = dict(row)

    # attachments 같이 묶음 — modality viewer 가 한 번에 받게.
    att_res = await session.execute(
        text("""
            SELECT id, role, mime_type, file_size, file_hash, caption, width, height
            FROM attachments
            WHERE item_id = :id
            ORDER BY created_at ASC
        """),
        {"id": item_id},
    )
    item["attachments"] = [dict(r) for r in att_res.mappings().all()]

    # D10.5 세션 A — 이 자료가 속한 wiki 페이지 목록 (graph 우측 패널 inline 용).
    # 정렬: 자기 정체성(self/primary) → 합성 완료(completed) → confidence.
    # body 는 무거워 미포함 — 프런트가 선택 slug 로 GET /wiki/{slug} 별도 조회.
    wiki_res = await session.execute(
        text("""
            SELECT wp.slug, wp.title, wp.body_status,
                   wpi.role, wpi.confidence
            FROM wiki_page_items wpi
            JOIN wiki_pages wp ON wp.id = wpi.wiki_page_id
            WHERE wpi.item_id = :id
            ORDER BY
                CASE WHEN wpi.role IN ('self', 'primary') THEN 0 ELSE 1 END,
                CASE WHEN wp.body_status = 'completed' THEN 0 ELSE 1 END,
                wpi.confidence DESC NULLS LAST,
                wp.slug
        """),
        {"id": item_id},
    )
    item["wikis"] = [dict(r) for r in wiki_res.mappings().all()]
    return item


async def update_item_user_notes(
    session: AsyncSession, *, item_id: UUID, user_notes: str | None,
) -> bool:
    """user_notes + user_notes_updated_at 갱신 (덮어쓰기).

    `user_notes=""` (빈 문자열) 면 NULL 로 정규화 (DB 일관성). user_notes_updated_at
    은 변경 있을 때만 now() 로 자동.

    Returns: True 면 row 변경됨, False 면 id 가 없거나 변경 없음.

    Note: 텔레그램·인박스 등에서 "같은 URL 에 새 caption" 시나리오는
    `append_item_user_notes` 를 써서 기존 메모를 보존한다. 이 함수는 사용자가
    UI 로 명시 편집할 때 등 의도적 덮어쓰기 경로용.
    """
    normalized = user_notes if user_notes else None
    res = await session.execute(
        text("""
            UPDATE items
            SET user_notes = :notes,
                user_notes_updated_at = now()
            WHERE id = :id
              AND COALESCE(user_notes, '') IS DISTINCT FROM COALESCE(:notes, '')
        """),
        {"id": item_id, "notes": normalized},
    )
    return (res.rowcount or 0) > 0


async def append_item_user_notes(
    session: AsyncSession, *, item_id: UUID, new_note: str | None,
) -> bool:
    """user_notes 에 새 caption 추가 — 기존 메모 보존 (Phase 2.5 wave-3 정책).

    같은 URL/파일을 텔레그램에 다시 던지면서 새 caption 을 붙이면, 기존 메모를
    덮지 않고 timestamp 구분자와 함께 append. 학습 데이터 비전 (§1) 상 사용자의
    누적 메모는 사라지면 안 됨.

    동작:
      - new_note 가 빈/None 이면 no-op (False)
      - 기존 user_notes 가 NULL/빈 이면 그대로 set (timestamp 안 붙임)
      - 기존 user_notes 안에 같은 new_note 가 이미 들어있으면 no-op (idempotent —
        텔레그램 retry 안전)
      - 그 외에는 `<기존>\n\n--- YYYY-MM-DD HH:MM ---\n<new_note>` 로 append

    Returns: True 면 row 변경됨, False 면 변경 없음.
    """
    if not new_note or not new_note.strip():
        return False
    note = new_note.strip()
    res = await session.execute(
        text("""
            UPDATE items
            SET user_notes = CASE
                    WHEN COALESCE(user_notes, '') = '' THEN :note
                    ELSE user_notes || E'\n\n--- ' ||
                         to_char(now(), 'YYYY-MM-DD HH24:MI') ||
                         E' ---\n' || :note
                END,
                user_notes_updated_at = now()
            WHERE id = :id
              AND POSITION(:note IN COALESCE(user_notes, '')) = 0
        """),
        {"id": item_id, "note": note},
    )
    return (res.rowcount or 0) > 0


async def add_alt_url_to_item(
    session: AsyncSession, *, item_id: UUID, new_url: str | None,
) -> bool:
    """dedup 시 신규 source_url 을 items.source_metadata['alt_urls'] 에 누적.

    배경 (2026-05-27): 같은 raw_content 가 여러 URL 로 ingest 될 수 있음 (예:
    GeekNews 토픽 = share.google URL = hada.io/topic URL). §2 idempotent 로
    새 item 안 만들지만, 신규 URL 정보가 어디에도 보존 안 되면 사용자가 그 URL
    로 검색 시 0건. → source_metadata.alt_urls 배열에 누적 + wiki list 검색이
    alt_urls 도 매칭.

    동작:
      - new_url 빈 값 / None / source_url 과 같음 → no-op
      - 이미 alt_urls 에 같은 URL 있으면 → no-op (idempotent)
      - 그 외에는 alt_urls 배열에 append

    Returns: True 면 row 변경됨.
    """
    if not new_url or not new_url.strip():
        return False
    url = new_url.strip()
    res = await session.execute(
        text("""
            UPDATE items
            SET source_metadata = jsonb_set(
                COALESCE(source_metadata::jsonb, '{}'::jsonb),
                '{alt_urls}',
                (COALESCE(source_metadata::jsonb -> 'alt_urls', '[]'::jsonb)
                 || to_jsonb(CAST(:url AS TEXT))),
                true
            )
            WHERE id = :id
              AND COALESCE(source_url, '') != CAST(:url AS TEXT)
              AND NOT (COALESCE(source_metadata::jsonb -> 'alt_urls', '[]'::jsonb) ? CAST(:url AS TEXT))
        """),
        {"id": item_id, "url": url},
    )
    return (res.rowcount or 0) > 0


async def merge_source_metadata(
    session: AsyncSession, *, item_id: UUID, extra: dict[str, Any],
) -> bool:
    """source_metadata 에 새 키들 merge — 최상위 jsonb concat.

    D12 wave 의 slack permalink 보강용. 기존 source_metadata 와 ``extra`` 를
    jsonb || 연산자로 합쳐 같은 키는 덮어쓴다. **중첩 dict 의 deep merge 가
    아닌 top-level merge** — Slack 의 경우 'slack' 키 하나가 그대로 들어가니
    충분.

    Returns: True 면 row 변경됨, False 면 id 가 없거나 extra 가 비어있음.
    """
    if not extra:
        return False
    import json as _json
    res = await session.execute(
        text("""
            UPDATE items
            SET source_metadata = source_metadata::jsonb || CAST(:extra AS jsonb)
            WHERE id = :id
        """),
        {"id": item_id, "extra": _json.dumps(extra)},
    )
    return (res.rowcount or 0) > 0


async def update_item_read(
    session: AsyncSession, *, item_id: UUID, is_read: bool,
) -> bool:
    """is_read 토글. is_read=True 로 처음 만들 때 read_at 을 now() 로 채움.

    is_read=False 로 다시 돌려도 read_at 은 보존 — "처음 읽은 시각" history.

    Returns: True 면 row 변경됨, False 면 id 가 없거나 이미 같은 값.
    """
    res = await session.execute(
        text("""
            UPDATE items
            SET is_read = :is_read,
                read_at = CASE
                    WHEN :is_read = TRUE AND read_at IS NULL THEN now()
                    ELSE read_at
                END
            WHERE id = :id
              AND is_read IS DISTINCT FROM :is_read
        """),
        {"id": item_id, "is_read": is_read},
    )
    return (res.rowcount or 0) > 0


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
# topics  +  item_topics  (지식 단위 그룹핑)
# ──────────────────────────────────────────────────────────────


async def find_topic_by_slug(
    session: AsyncSession, slug: str,
) -> dict[str, Any] | None:
    res = await session.execute(
        text("""
            SELECT id, slug, title, description, primary_external_id, tags,
                   created_at, updated_at
            FROM topics WHERE slug = :s
        """),
        {"s": slug},
    )
    row = res.mappings().one_or_none()
    return dict(row) if row else None


async def create_topic(
    session: AsyncSession,
    *,
    slug: str,
    title: str,
    primary_external_id: dict[str, str] | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    res = await session.execute(
        text("""
            INSERT INTO topics (slug, title, description, primary_external_id, tags)
            VALUES (
                :slug, :title, :description,
                CAST(:peid AS JSONB),
                COALESCE(:tags, '{}'::text[])
            )
            RETURNING id, slug, title, description, primary_external_id, tags,
                      created_at, updated_at
        """),
        {
            "slug": slug,
            "title": title,
            "description": description,
            "peid": _to_json(primary_external_id) if primary_external_id is not None else None,
            "tags": tags,
        },
    )
    return dict(res.mappings().one())


async def find_or_create_topic(
    session: AsyncSession,
    *,
    slug: str,
    title: str,
    primary_external_id: dict[str, str] | None = None,
) -> tuple[dict[str, Any], bool]:
    """slug 기준 upsert. 반환 (topic, created)."""
    existing = await find_topic_by_slug(session, slug)
    if existing is not None:
        return existing, False
    topic = await create_topic(
        session,
        slug=slug,
        title=title,
        primary_external_id=primary_external_id,
    )
    return topic, True


async def link_item_to_topic(
    session: AsyncSession,
    *,
    item_id: UUID,
    topic_id: UUID,
    role: str,
    confidence: float = 1.0,
    source: str = "auto",
    note: str | None = None,
) -> bool:
    """item ↔ topic link. 이미 있으면 role/source/note 만 갱신 (manual 이 auto 를 덮음).

    반환: 새로 만들어졌으면 True, 기존이면 False.
    """
    # auto 가 기존 manual 을 덮어쓰면 안 되므로 source 우선순위 보호:
    # 새 source 가 manual 이거나, 기존이 auto 면 갱신. 그 외엔 들어온 정보는 무시.
    res = await session.execute(
        text("""
            INSERT INTO item_topics (item_id, topic_id, role, confidence, source, note)
            VALUES (:item_id, :topic_id, :role, :conf, :source, :note)
            ON CONFLICT (item_id, topic_id) DO UPDATE
                SET role = CASE
                        WHEN EXCLUDED.source = 'manual' OR item_topics.source = 'auto'
                        THEN EXCLUDED.role ELSE item_topics.role
                    END,
                    confidence = GREATEST(item_topics.confidence, EXCLUDED.confidence),
                    source = CASE
                        WHEN EXCLUDED.source = 'manual' THEN 'manual'
                        ELSE item_topics.source
                    END,
                    note = COALESCE(EXCLUDED.note, item_topics.note)
            RETURNING (xmax = 0) AS inserted
        """),
        {
            "item_id": item_id, "topic_id": topic_id, "role": role,
            "conf": confidence, "source": source, "note": note,
        },
    )
    row = res.first()
    return bool(row and row[0])


async def list_items_for_topic(
    session: AsyncSession, *, topic_id: UUID,
) -> list[dict[str, Any]]:
    res = await session.execute(
        text("""
            SELECT i.id, i.source_type, i.source_url, i.title, i.summary,
                   i.tags, it.role, it.confidence, it.source, it.note,
                   i.ingested_at
            FROM item_topics it
            JOIN items i ON i.id = it.item_id
            WHERE it.topic_id = :tid
            ORDER BY it.role, i.ingested_at
        """),
        {"tid": topic_id},
    )
    return [dict(r) for r in res.mappings().all()]


async def list_topics_for_item(
    session: AsyncSession, *, item_id: UUID,
) -> list[dict[str, Any]]:
    res = await session.execute(
        text("""
            SELECT t.id, t.slug, t.title, t.primary_external_id, t.tags,
                   it.role, it.confidence, it.source
            FROM item_topics it
            JOIN topics t ON t.id = it.topic_id
            WHERE it.item_id = :iid
        """),
        {"iid": item_id},
    )
    return [dict(r) for r in res.mappings().all()]


async def list_topics(
    session: AsyncSession, *, limit: int = 50,
) -> list[dict[str, Any]]:
    res = await session.execute(
        text("""
            SELECT t.id, t.slug, t.title, t.primary_external_id, t.tags,
                   t.created_at, t.updated_at,
                   COUNT(it.item_id) AS item_count
            FROM topics t
            LEFT JOIN item_topics it ON it.topic_id = t.id
            GROUP BY t.id
            ORDER BY t.updated_at DESC
            LIMIT :lim
        """),
        {"lim": limit},
    )
    return [dict(r) for r in res.mappings().all()]


async def list_topics_by_ids(
    session: AsyncSession, *, topic_ids: list[UUID],
) -> list[dict[str, Any]]:
    """주어진 topic id list 의 topic row 만 fetch (LIMIT 없음).

    graph endpoint 들이 "특정 topic 들의 full row 필요" 케이스에 사용. 옛 패턴은
    list_topics(limit=500) 으로 전체 가져온 후 dict lookup — limit 밖의 topic 은
    조용히 빠져 frontend force-graph 의 'node not found' runtime error 유발.
    이 함수는 그 dangling node 문제 근본 fix (2026-05-25).

    빈 list 입력 → 빈 응답 (DB roundtrip 없이).
    """
    if not topic_ids:
        return []
    res = await session.execute(
        text("""
            SELECT t.id, t.slug, t.title, t.primary_external_id, t.tags,
                   t.created_at, t.updated_at,
                   COUNT(it.item_id) AS item_count
            FROM topics t
            LEFT JOIN item_topics it ON it.topic_id = t.id
            WHERE t.id = ANY(:tids)
            GROUP BY t.id
        """),
        {"tids": topic_ids},
    )
    return [dict(r) for r in res.mappings().all()]


# ──────────────────────────────────────────────────────────────
# categories  +  topic_categories  (키워드 카테고리 노드 계층)
# ──────────────────────────────────────────────────────────────


async def find_category_by_slug(
    session: AsyncSession, *, slug: str,
) -> dict[str, Any] | None:
    """slug 로 category 1개 조회 + topic_count / item_count 동봉.

    list_categories 와 동일한 집계를 한 카테고리에 대해서만 수행 — graph expand
    응답의 카테고리 노드가 0/0 으로 표시되던 버그 (Phase 2.5 wave-3) 해결.
    """
    res = await session.execute(
        text("""
            SELECT c.id, c.slug, c.label, c.description, c.synonyms, c.color,
                   c.pinned, c.created_at, c.updated_at,
                   COUNT(DISTINCT tc.topic_id) AS topic_count,
                   COUNT(DISTINCT it.item_id)  AS item_count
              FROM categories c
              LEFT JOIN topic_categories tc ON tc.category_id = c.id
              LEFT JOIN item_topics it ON it.topic_id = tc.topic_id
             WHERE c.slug = :slug
             GROUP BY c.id
        """),
        {"slug": slug},
    )
    row = res.mappings().first()
    return dict(row) if row else None


async def upsert_category(
    session: AsyncSession, *,
    slug: str,
    label: str,
    description: str | None = None,
    synonyms: list[str] | None = None,
    color: str | None = None,
    pinned: bool = False,
) -> UUID:
    """category INSERT or UPDATE — slug UNIQUE 충돌 시 label/synonyms 등 update.

    synonyms 는 union (기존 synonyms ∪ 새 synonyms) — 사용자가 추가하면 잃지 않음.
    label/description/color/pinned 는 새 값으로 덮어쓰기 (명시적 갱신 의도).
    """
    res = await session.execute(
        text("""
            INSERT INTO categories (slug, label, description, synonyms, color, pinned)
            VALUES (:slug, :label, :desc, :syn, :color, :pinned)
            ON CONFLICT (slug) DO UPDATE
                SET label = EXCLUDED.label,
                    description = COALESCE(EXCLUDED.description, categories.description),
                    synonyms = (
                        SELECT array_agg(DISTINCT s)
                          FROM unnest(categories.synonyms || EXCLUDED.synonyms) AS s
                         WHERE s IS NOT NULL AND s <> ''
                    ),
                    color = COALESCE(EXCLUDED.color, categories.color),
                    pinned = EXCLUDED.pinned
            RETURNING id
        """),
        {
            "slug": slug, "label": label, "desc": description,
            "syn": synonyms or [], "color": color, "pinned": pinned,
        },
    )
    return res.scalar_one()


async def list_categories(
    session: AsyncSession, *, limit: int = 1000,
) -> list[dict[str, Any]]:
    """카테고리 + 그 안의 topic 수 + item 수 (graph 노드 크기 결정).

    그래프에서 카테고리 노드의 size 는 topic 수에 비례 — 큰 카테고리 (많은 자료)
    가 시각적으로 눈에 띄게.
    """
    res = await session.execute(
        text("""
            SELECT c.id, c.slug, c.label, c.description, c.synonyms, c.color,
                   c.pinned, c.created_at, c.updated_at,
                   COUNT(DISTINCT tc.topic_id) AS topic_count,
                   COUNT(DISTINCT it.item_id)  AS item_count
              FROM categories c
              LEFT JOIN topic_categories tc ON tc.category_id = c.id
              LEFT JOIN item_topics it ON it.topic_id = tc.topic_id
             GROUP BY c.id
             ORDER BY c.pinned DESC, topic_count DESC, c.updated_at DESC
             LIMIT :lim
        """),
        {"lim": limit},
    )
    return [dict(r) for r in res.mappings().all()]


async def list_topics_in_category(
    session: AsyncSession, *, category_id: UUID, limit: int = 500,
) -> list[dict[str, Any]]:
    """특정 카테고리에 속한 topic 들 — UI 에서 카테고리 노드 클릭 시 expand."""
    res = await session.execute(
        text("""
            SELECT t.id, t.slug, t.title, t.primary_external_id, t.tags,
                   t.created_at, t.updated_at,
                   COUNT(it.item_id) AS item_count
              FROM topic_categories tc
              JOIN topics t ON t.id = tc.topic_id
              LEFT JOIN item_topics it ON it.topic_id = t.id
             WHERE tc.category_id = :cid
             GROUP BY t.id
             ORDER BY item_count DESC, t.updated_at DESC
             LIMIT :lim
        """),
        {"cid": category_id, "lim": limit},
    )
    return [dict(r) for r in res.mappings().all()]


async def link_topic_to_category(
    session: AsyncSession, *,
    topic_id: UUID,
    category_id: UUID,
    source: str = "auto",
    confidence: float = 1.0,
) -> bool:
    """topic ↔ category 매핑. 이미 있으면 source/confidence 갱신 (manual > auto).

    Returns: True 면 row 변경 (insert 또는 update), False 면 동일.
    """
    res = await session.execute(
        text("""
            INSERT INTO topic_categories (topic_id, category_id, source, confidence)
            VALUES (:t, :c, :src, :conf)
            ON CONFLICT (topic_id, category_id) DO UPDATE
                SET source = CASE
                        WHEN EXCLUDED.source = 'manual' OR topic_categories.source = 'auto'
                        THEN EXCLUDED.source ELSE topic_categories.source
                    END,
                    confidence = GREATEST(topic_categories.confidence, EXCLUDED.confidence)
        """),
        {"t": topic_id, "c": category_id, "src": source, "conf": confidence},
    )
    return (res.rowcount or 0) > 0


async def list_categories_for_topic(
    session: AsyncSession, *, topic_id: UUID,
) -> list[dict[str, Any]]:
    """topic 한 개에 매핑된 categories — UI 에서 topic 노드 hover 시 표시."""
    res = await session.execute(
        text("""
            SELECT c.id, c.slug, c.label, c.color, c.pinned,
                   tc.source, tc.confidence
              FROM topic_categories tc
              JOIN categories c ON c.id = tc.category_id
             WHERE tc.topic_id = :tid
             ORDER BY c.pinned DESC, c.label
        """),
        {"tid": topic_id},
    )
    return [dict(r) for r in res.mappings().all()]


async def list_topic_category_links(
    session: AsyncSession, *, category_ids: list[UUID] | None = None,
) -> list[dict[str, Any]]:
    """graph 빌드용 — category ↔ topic 엣지 dump.

    category_ids 명시하면 그 카테고리들의 link 만, 없으면 전체.
    """
    if category_ids is not None:
        if not category_ids:
            return []
        res = await session.execute(
            text("""
                SELECT topic_id, category_id, source, confidence
                  FROM topic_categories
                 WHERE category_id = ANY(:ids)
            """),
            {"ids": category_ids},
        )
    else:
        res = await session.execute(
            text("""
                SELECT topic_id, category_id, source, confidence
                  FROM topic_categories
            """),
        )
    return [dict(r) for r in res.mappings().all()]


# ──────────────────────────────────────────────────────────────
# Graph helpers (Phase 2.5 wave-3) — cytoscape JSON 빌드용
# ──────────────────────────────────────────────────────────────


async def list_items_summary(
    session: AsyncSession,
    *,
    item_ids: list[UUID] | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """graph 노드용 item 요약 — raw_content 없이 가벼운 필드만.

    item_ids 명시하면 그 id 들만, 없으면 최근 limit 개 (ingested_at DESC).
    graph UI 노드 표시 + 색상/모양 결정에 필요한 정보:
    - source_type (pdf/url/youtube/github/document/telegram/...)
    - is_read / user_notes 존재 여부 (UI 의 unread 표시 + 메모 indicator)
    - tags (graph 의 tag 기반 그룹화)
    """
    if item_ids is not None:
        if not item_ids:
            return []
        res = await session.execute(
            text("""
                SELECT id, source_type, source_url, title, summary, tags,
                       is_read, (user_notes IS NOT NULL AND user_notes != '') AS has_notes,
                       ingested_at
                FROM items
                WHERE id = ANY(:ids)
                ORDER BY ingested_at DESC
            """),
            {"ids": list(item_ids)},
        )
    else:
        res = await session.execute(
            text("""
                SELECT id, source_type, source_url, title, summary, tags,
                       is_read, (user_notes IS NOT NULL AND user_notes != '') AS has_notes,
                       ingested_at
                FROM items
                ORDER BY ingested_at DESC
                LIMIT :lim
            """),
            {"lim": limit},
        )
    return [dict(r) for r in res.mappings().all()]


async def list_item_topic_links(
    session: AsyncSession,
    *,
    item_ids: list[UUID] | None = None,
    topic_ids: list[UUID] | None = None,
) -> list[dict[str, Any]]:
    """item ↔ topic 연결 일괄 조회 — graph 엣지 빌드용.

    필터:
    - item_ids 만 명시 → 그 item 들의 모든 topic link
    - topic_ids 만 명시 → 그 topic 들의 모든 item link
    - 둘 다 명시 → 교집합
    - 둘 다 None → 전체 (대량 데이터 시 위험 — 위치별로 호출 시 명시)

    반환 필드: item_id, topic_id, role, confidence, source
    """
    conds: list[str] = []
    params: dict[str, Any] = {}
    if item_ids:
        conds.append("item_id = ANY(:iids)")
        params["iids"] = list(item_ids)
    if topic_ids:
        conds.append("topic_id = ANY(:tids)")
        params["tids"] = list(topic_ids)
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    res = await session.execute(
        text(f"""
            SELECT item_id, topic_id, role, confidence, source
            FROM item_topics
            {where}
        """),
        params,
    )
    return [dict(r) for r in res.mappings().all()]


async def search_items_by_text(
    session: AsyncSession, *, query: str, limit: int = 50,
) -> list[UUID]:
    """Postgres FTS 기반 빠른 item id 검색 — graph subset 용.

    Qdrant 벡터 검색을 굳이 graph endpoint 에 끌어들이지 않음 (chunk 단위 결과의
    item dedup 비용 + 임베딩 로드). FTS 로 빠른 후보 추리 + UI 가 검색 결과를
    graph 위에 highlight.
    """
    if not query.strip():
        return []
    res = await session.execute(
        text("""
            SELECT id
            FROM items
            WHERE fts_vector @@ websearch_to_tsquery('simple', :q)
            ORDER BY ts_rank(fts_vector, websearch_to_tsquery('simple', :q)) DESC
            LIMIT :lim
        """),
        {"q": query, "lim": limit},
    )
    return [r[0] for r in res.all()]


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
# D10.5 세션 B — keyword ▸ wiki ▸ item 그래프 (categories 대체)
# 그룹 축을 wiki_pages.keywords (정규화 영문, D10.6) 로 통일.
# ──────────────────────────────────────────────────────────────


# garbage 키워드 제외 (빈 값 / 순수 대시·구두점·공백) — wiki.py 와 동일 정책.
_KW_GRAPH_GARBAGE = (
    "TRIM(keyword) <> '' AND keyword !~ '^[-_.[:space:][:punct:]]+$'"
)

_LIST_KEYWORD_COUNTS_SQL = text(f"""
    SELECT keyword, COUNT(*) AS usage_count
    FROM wiki_pages, UNNEST(keywords) AS keyword
    WHERE (CAST(:q AS TEXT) IS NULL OR keyword ILIKE '%' || CAST(:q AS TEXT) || '%')
      AND {_KW_GRAPH_GARBAGE}
    GROUP BY keyword
    ORDER BY usage_count DESC, keyword ASC
    LIMIT :limit
""")


async def list_keyword_counts(
    session: AsyncSession, *, limit: int = 200, q: str | None = None,
) -> list[dict[str, Any]]:
    """distinct wiki keyword + 사용 wiki 수 (빈도순). 그래프의 keyword 그룹 노드용."""
    res = await session.execute(_LIST_KEYWORD_COUNTS_SQL, {"q": q, "limit": limit})
    return [dict(r) for r in res.mappings().all()]


# wiki 노드 공통 SELECT — keyword/slug/title + topic 의 primary_external_id(색) +
# 살아있는 source(item) 수. WHERE 만 호출처가 결정.
_WIKI_NODE_SELECT = """
    SELECT wp.id, wp.slug, wp.title, wp.keywords,
           t.primary_external_id,
           (SELECT COUNT(*) FROM wiki_page_items wpi
              WHERE wpi.wiki_page_id = wp.id
                AND (wpi.user_action IS NULL OR wpi.user_action != 'removed')
           ) AS item_count
      FROM wiki_pages wp
      LEFT JOIN topics t ON t.id = wp.topic_id
"""

_WIKIS_FOR_KEYWORD_SQL = text(f"""
    {_WIKI_NODE_SELECT}
     WHERE COALESCE(wp.keywords, ARRAY[]::text[]) @> ARRAY[:kw]::text[]
     ORDER BY wp.is_pinned DESC, item_count DESC, LOWER(wp.title) ASC
     LIMIT :limit
""")


async def list_wikis_for_keyword(
    session: AsyncSession, *, keyword: str, limit: int = 300,
) -> list[dict[str, Any]]:
    """그 keyword 를 가진 wiki 들 (keyword expand 시)."""
    res = await session.execute(_WIKIS_FOR_KEYWORD_SQL, {"kw": keyword, "limit": limit})
    return [dict(r) for r in res.mappings().all()]


# 실시간 co-occurrence — kw 를 가진 wiki 들에서 함께 등장한 다른 키워드 + 공유 횟수.
# GIN index (keywords @> ARRAY[kw]) 로 빠르고, 새 자료가 들어오면 다음 호출에 자동
# 반영 (동적). 전역 배치 그룹화 대신 "클릭한 키워드 중심" 지역 클러스터.
_COOCCUR_KEYWORDS_SQL = text(f"""
    SELECT other_kw AS keyword, COUNT(*) AS shared
    FROM wiki_pages wp, UNNEST(wp.keywords) AS other_kw
    WHERE wp.keywords @> ARRAY[:kw]::text[]
      AND other_kw <> :kw
      AND TRIM(other_kw) <> '' AND other_kw !~ '^[-_.[:space:][:punct:]]+$'
    GROUP BY other_kw
    HAVING COUNT(*) >= :threshold
    ORDER BY shared DESC, other_kw ASC
    LIMIT :limit
""")


async def list_cooccurring_keywords(
    session: AsyncSession, *, keyword: str, threshold: int = 2, limit: int = 30,
) -> list[dict[str, Any]]:
    """keyword 와 같은 wiki 를 threshold 개 이상 공유한 다른 키워드 (공유순).

    실시간 — 새 wiki 가 들어와도 다음 호출에 반영 (동적). 흔한 키워드의 거대
    연결을 threshold 로 완화.
    """
    res = await session.execute(
        _COOCCUR_KEYWORDS_SQL,
        {"kw": keyword, "threshold": threshold, "limit": limit},
    )
    return [dict(r) for r in res.mappings().all()]


async def list_wiki_nodes(
    session: AsyncSession, *, ids: list[UUID] | None = None,
    slugs: list[str] | None = None,
) -> list[dict[str, Any]]:
    """wiki 노드 행을 id 또는 slug 로 정확 fetch (LIMIT 없음 — dangling edge 방지)."""
    if ids:
        sql = text(f"{_WIKI_NODE_SELECT} WHERE wp.id = ANY(:ids)")
        res = await session.execute(sql, {"ids": list(ids)})
    elif slugs:
        sql = text(f"{_WIKI_NODE_SELECT} WHERE wp.slug = ANY(:slugs)")
        res = await session.execute(sql, {"slugs": list(slugs)})
    else:
        return []
    return [dict(r) for r in res.mappings().all()]


async def list_wiki_item_links(
    session: AsyncSession, *, wiki_page_ids: list[UUID] | None = None,
    item_ids: list[UUID] | None = None,
) -> list[dict[str, Any]]:
    """wiki_page_items 덤프 (wiki↔item 엣지용). wiki_page_ids 또는 item_ids 로 필터.

    둘 다 None 이면 전체 덤프는 막고 빈 리스트 (안전). 'removed' 는 제외.
    """
    if wiki_page_ids is None and item_ids is None:
        return []
    clauses = ["(wpi.user_action IS NULL OR wpi.user_action != 'removed')"]
    params: dict[str, Any] = {}
    if wiki_page_ids is not None:
        clauses.append("wpi.wiki_page_id = ANY(:wids)")
        params["wids"] = list(wiki_page_ids)
    if item_ids is not None:
        clauses.append("wpi.item_id = ANY(:iids)")
        params["iids"] = list(item_ids)
    sql = text(f"""
        SELECT wpi.wiki_page_id, wpi.item_id, wpi.role, wpi.confidence, wpi.source
          FROM wiki_page_items wpi
         WHERE {" AND ".join(clauses)}
    """)
    res = await session.execute(sql, params)
    return [dict(r) for r in res.mappings().all()]


# ──────────────────────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────────────────────


def _to_json(d: dict[str, Any]) -> str:
    """psycopg/asyncpg에 JSONB로 전달하기 위한 직렬화."""
    import orjson
    return orjson.dumps(d).decode("utf-8")


# ──────────────────────────────────────────────────────────────
# auth / multitenant — users / spaces / space_members (2026-06-03 단계 A)
# ──────────────────────────────────────────────────────────────


async def count_users(session: AsyncSession) -> int:
    """전체 user 수 — bootstrap(첫 관리자 등록) 가능 여부 판단용."""
    res = await session.execute(text("SELECT COUNT(*) FROM users"))
    return int(res.scalar_one())


async def get_user_by_email(
    session: AsyncSession, *, email: str,
) -> dict[str, Any] | None:
    """email 로 user 조회 (로그인용 — password_hash 포함)."""
    res = await session.execute(
        text(
            "SELECT id, email, password_hash, display_name, must_change_password "
            "FROM users WHERE email = :e"
        ),
        {"e": email},
    )
    row = res.mappings().one_or_none()
    return dict(row) if row else None


async def get_user_by_id(
    session: AsyncSession, *, user_id: UUID,
) -> dict[str, Any] | None:
    res = await session.execute(
        text(
            "SELECT id, email, display_name, must_change_password "
            "FROM users WHERE id = :id"
        ),
        {"id": user_id},
    )
    row = res.mappings().one_or_none()
    return dict(row) if row else None


async def create_user(
    session: AsyncSession, *,
    email: str, password_hash: str, display_name: str | None = None,
    must_change_password: bool = False,
) -> UUID:
    res = await session.execute(
        text("""
            INSERT INTO users (email, password_hash, display_name, must_change_password)
            VALUES (:e, :ph, :dn, :mcp)
            RETURNING id
        """),
        {"e": email, "ph": password_hash, "dn": display_name, "mcp": must_change_password},
    )
    return res.scalar_one()


async def update_user_credentials(
    session: AsyncSession, *,
    user_id: UUID,
    new_email: str | None = None,
    new_password_hash: str | None = None,
) -> None:
    """첫 로그인 강제 변경 — 이메일/비번 갱신 + must_change_password=false.
    new_email/new_password_hash 중 제공된 것만 갱신."""
    sets = ["must_change_password = false"]
    params: dict[str, Any] = {"id": user_id}
    if new_email is not None:
        sets.append("email = :e")
        params["e"] = new_email
    if new_password_hash is not None:
        sets.append("password_hash = :ph")
        params["ph"] = new_password_hash
    await session.execute(
        text(f"UPDATE users SET {', '.join(sets)} WHERE id = :id"), params
    )


async def create_space(
    session: AsyncSession, *, name: str, kind: str = "personal",
) -> UUID:
    res = await session.execute(
        text("INSERT INTO spaces (name, kind) VALUES (:n, :k) RETURNING id"),
        {"n": name, "k": kind},
    )
    return res.scalar_one()


async def get_space(
    session: AsyncSession, *, space_id: UUID,
) -> dict[str, Any] | None:
    res = await session.execute(
        text("SELECT id, name, kind FROM spaces WHERE id = :id"),
        {"id": space_id},
    )
    row = res.mappings().one_or_none()
    return dict(row) if row else None


async def add_member(
    session: AsyncSession, *,
    space_id: UUID, user_id: UUID, role: str = "owner",
) -> None:
    await session.execute(
        text("""
            INSERT INTO space_members (space_id, user_id, role)
            VALUES (:s, :u, :r)
            ON CONFLICT (space_id, user_id) DO NOTHING
        """),
        {"s": space_id, "u": user_id, "r": role},
    )


async def is_space_member(
    session: AsyncSession, *, space_id: UUID, user_id: UUID,
) -> bool:
    res = await session.execute(
        text("SELECT 1 FROM space_members WHERE space_id = :s AND user_id = :u"),
        {"s": space_id, "u": user_id},
    )
    return res.scalar_one_or_none() is not None


async def get_member_role(
    session: AsyncSession, *, space_id: UUID, user_id: UUID,
) -> str | None:
    """user 의 그 space 내 역할 (owner|admin|member). 멤버 아니면 None."""
    res = await session.execute(
        text("SELECT role FROM space_members WHERE space_id = :s AND user_id = :u"),
        {"s": space_id, "u": user_id},
    )
    return res.scalar_one_or_none()


async def list_space_members(
    session: AsyncSession, *, space_id: UUID,
) -> list[dict[str, Any]]:
    """space 의 멤버 목록 (가입순) — 루트 관리자 유저 관리 UI 용."""
    res = await session.execute(
        text("""
            SELECT u.id, u.email, u.display_name, m.role, m.created_at
            FROM space_members m
            JOIN users u ON u.id = m.user_id
            WHERE m.space_id = :s
            ORDER BY m.created_at ASC
        """),
        {"s": space_id},
    )
    return [dict(r) for r in res.mappings().all()]


async def delete_user(session: AsyncSession, *, user_id: UUID) -> None:
    """user 삭제 (space_members 는 CASCADE). 루트가 멤버 제거 시."""
    await session.execute(text("DELETE FROM users WHERE id = :u"), {"u": user_id})


async def list_user_spaces(
    session: AsyncSession, *, user_id: UUID,
) -> list[dict[str, Any]]:
    """user 가 속한 space 목록 (가입순). 첫 항목이 기본 활성 space 후보."""
    res = await session.execute(
        text("""
            SELECT s.id, s.name, s.kind, m.role
            FROM space_members m
            JOIN spaces s ON s.id = m.space_id
            WHERE m.user_id = :u
            ORDER BY m.created_at ASC
        """),
        {"u": user_id},
    )
    return [dict(r) for r in res.mappings().all()]


# ──────────────────────────────────────────────────────────────
# ask 대화 세션 (2026-06-03 단계 B) — write-through 미러 + 본인 조회
# ──────────────────────────────────────────────────────────────


async def sync_user_ask_store(
    session: AsyncSession, *,
    user_id: UUID, space_id: UUID,
    projects: list[dict[str, Any]], sessions: list[dict[str, Any]],
) -> None:
    """이 user 의 기존 세션/프로젝트 전부 삭제 후 재삽입 (full replace, messages 는 CASCADE).

    프라이버시: 세션은 소유자(user_id)에 묶이고, space_id 는 학습 범위로 기록한다.
    """
    await session.execute(text("DELETE FROM ask_sessions WHERE user_id = :u"), {"u": user_id})
    await session.execute(text("DELETE FROM ask_projects WHERE user_id = :u"), {"u": user_id})

    for p in projects:
        await session.execute(
            text("""
                INSERT INTO ask_projects (id, user_id, space_id, name, created_at_ms)
                VALUES (:id, :u, :s, :n, :c)
            """),
            {"id": p["id"], "u": user_id, "s": space_id, "n": p["name"], "c": p.get("createdAt")},
        )

    for sess in sessions:
        await session.execute(
            text("""
                INSERT INTO ask_sessions
                    (id, user_id, space_id, title, project_id, created_at_ms, updated_at_ms)
                VALUES (:id, :u, :s, :t, :pid, :c, :upd)
            """),
            {"id": sess["id"], "u": user_id, "s": space_id, "t": sess.get("title"),
             "pid": sess.get("projectId"), "c": sess.get("createdAt"), "upd": sess.get("updatedAt")},
        )
        for i, m in enumerate(sess.get("messages", [])):
            await session.execute(
                text("""
                    INSERT INTO ask_messages
                        (session_id, ord, role, content, ts, llm_model, citations, related_wikis, ingested)
                    VALUES (:sid, :ord, :role, :content, :ts, :model,
                            CAST(:cit AS JSONB), CAST(:rw AS JSONB), CAST(:ing AS JSONB))
                """),
                {"sid": sess["id"], "ord": i, "role": m["role"], "content": m["content"],
                 "ts": m.get("ts"), "model": m.get("llm_model"),
                 "cit": _to_json(m.get("citations") or []),
                 "rw": _to_json(m.get("related_wikis") or []),
                 "ing": _to_json(m.get("ingested") or [])},
            )


async def list_user_ask_sessions(
    session: AsyncSession, *, user_id: UUID,
) -> list[dict[str, Any]]:
    """본인 세션 목록 (메시지 제외). admin 도 본인 것만 — 프라이버시."""
    res = await session.execute(
        text("""
            SELECT s.id, s.title, s.project_id, s.created_at_ms, s.updated_at_ms,
                   (SELECT COUNT(*) FROM ask_messages m WHERE m.session_id = s.id) AS message_count
            FROM ask_sessions s
            WHERE s.user_id = :u
            ORDER BY s.updated_at_ms DESC NULLS LAST
        """),
        {"u": user_id},
    )
    return [dict(r) for r in res.mappings().all()]


async def get_user_ask_session(
    session: AsyncSession, *, user_id: UUID, session_id: str,
) -> dict[str, Any] | None:
    """본인 세션 상세 (메시지 포함). 남의 세션이면 None — 프라이버시 강제."""
    sres = await session.execute(
        text("""
            SELECT id, title, project_id, created_at_ms, updated_at_ms
            FROM ask_sessions WHERE id = :id AND user_id = :u
        """),
        {"id": session_id, "u": user_id},
    )
    srow = sres.mappings().one_or_none()
    if srow is None:
        return None
    d = dict(srow)
    mres = await session.execute(
        text("""
            SELECT ord, role, content, ts, llm_model, citations, related_wikis, ingested
            FROM ask_messages WHERE session_id = :id ORDER BY ord
        """),
        {"id": session_id},
    )
    d["messages"] = [dict(r) for r in mres.mappings().all()]
    return d


async def list_space_ask_projects(
    session: AsyncSession, *, space_id: UUID,
) -> list[dict[str, Any]]:
    """조직 공유 프로젝트 목록 (space 전체가 봄)."""
    res = await session.execute(
        text("""
            SELECT id, name, created_at_ms, user_id
            FROM ask_projects WHERE space_id = :s
            ORDER BY created_at_ms ASC NULLS LAST
        """),
        {"s": space_id},
    )
    return [dict(r) for r in res.mappings().all()]


async def get_user_ask_store_full(
    session: AsyncSession, *, user_id: UUID,
) -> list[dict[str, Any]]:
    """본인 모든 세션 + 메시지 (frontend localStorage 복원용). 개인 규모라 N+1 허용."""
    sres = await session.execute(
        text("""
            SELECT id, title, project_id, created_at_ms, updated_at_ms
            FROM ask_sessions WHERE user_id = :u
            ORDER BY updated_at_ms DESC NULLS LAST
        """),
        {"u": user_id},
    )
    out: list[dict[str, Any]] = []
    for srow in sres.mappings().all():
        s = dict(srow)
        mres = await session.execute(
            text("""
                SELECT ord, role, content, ts, llm_model, citations, related_wikis, ingested
                FROM ask_messages WHERE session_id = :id ORDER BY ord
            """),
            {"id": s["id"]},
        )
        s["messages"] = [dict(r) for r in mres.mappings().all()]
        out.append(s)
    return out


# ────────────────────────────────────────────────────────────────────────────
# collection_keywords — 키워드 기반 arxiv 수집용 관심 키워드 (admin, space 격리)
# ────────────────────────────────────────────────────────────────────────────

async def list_collection_keywords(
    session: AsyncSession, *, space_id: UUID,
) -> list[dict[str, Any]]:
    """space 의 수집 키워드 종합 목록 (등록자 display_name 포함)."""
    res = await session.execute(
        text("""
            SELECT ck.id, ck.keyword, ck.enabled, ck.user_id,
                   u.display_name, u.email, ck.created_at, ck.updated_at
            FROM collection_keywords ck
            LEFT JOIN users u ON u.id = ck.user_id
            WHERE ck.space_id = :s
            ORDER BY ck.created_at ASC
        """),
        {"s": space_id},
    )
    return [dict(r) for r in res.mappings().all()]


async def add_collection_keyword(
    session: AsyncSession, *, space_id: UUID, user_id: UUID, keyword: str,
) -> dict[str, Any] | None:
    """키워드 등록. 같은 space 에 동일 keyword 있으면 무시(ON CONFLICT). 새로 만든
    row 를 반환, 이미 있으면 None."""
    res = await session.execute(
        text("""
            INSERT INTO collection_keywords (space_id, user_id, keyword)
            VALUES (:s, :u, :k)
            ON CONFLICT (space_id, keyword) DO NOTHING
            RETURNING id, keyword, enabled, user_id, created_at, updated_at
        """),
        {"s": space_id, "u": user_id, "k": keyword},
    )
    row = res.mappings().first()
    return dict(row) if row else None


async def delete_collection_keyword(
    session: AsyncSession, *, space_id: UUID, keyword_id: UUID,
) -> bool:
    """키워드 삭제 (space 격리). 삭제됐으면 True."""
    res = await session.execute(
        text("DELETE FROM collection_keywords WHERE id = :id AND space_id = :s"),
        {"id": keyword_id, "s": space_id},
    )
    return (res.rowcount or 0) > 0


async def set_collection_keyword_enabled(
    session: AsyncSession, *, space_id: UUID, keyword_id: UUID, enabled: bool,
) -> bool:
    """키워드 enabled 토글 (space 격리). 갱신됐으면 True."""
    res = await session.execute(
        text("""
            UPDATE collection_keywords SET enabled = :e, updated_at = now()
            WHERE id = :id AND space_id = :s
        """),
        {"e": enabled, "id": keyword_id, "s": space_id},
    )
    return (res.rowcount or 0) > 0


# ────────────────────────────────────────────────────────────────────────────
# arxiv_papers — 전체 arXiv 메타 로컬 캐시 (rate limit 근본 해결). 로컬 FTS 검색.
# ────────────────────────────────────────────────────────────────────────────

_UPSERT_ARXIV_SQL = text("""
    INSERT INTO arxiv_papers (
        arxiv_id, title, abstract, authors, categories, version,
        published, updated, doi, journal_ref, source
    ) VALUES (
        :arxiv_id, :title, :abstract, :authors, :categories, :version,
        :published, :updated, :doi, :journal_ref, :source
    )
    ON CONFLICT (arxiv_id) DO UPDATE SET
        title       = EXCLUDED.title,
        abstract    = EXCLUDED.abstract,
        authors     = EXCLUDED.authors,
        categories  = EXCLUDED.categories,
        version     = EXCLUDED.version,
        published   = EXCLUDED.published,
        updated     = EXCLUDED.updated,
        doi         = EXCLUDED.doi,
        journal_ref = EXCLUDED.journal_ref,
        source      = EXCLUDED.source,
        fetched_at  = now()
    WHERE arxiv_papers.updated IS NULL
       OR EXCLUDED.updated IS NULL
       OR arxiv_papers.updated <= EXCLUDED.updated
""")


async def upsert_arxiv_papers(
    session: AsyncSession, rows: list[dict[str, Any]],
) -> int:
    """arxiv_papers 배치 upsert (OAI 증분용; Kaggle 초기 대량은 COPY 별도 job).
    더 새 버전(updated)만 갱신. rows 각 dict 는 컬럼 키 그대로. 반환 시도 건수."""
    if not rows:
        return 0
    await session.execute(_UPSERT_ARXIV_SQL, rows)   # executemany
    return len(rows)


async def count_arxiv_papers(session: AsyncSession) -> int:
    res = await session.execute(text("SELECT count(*) FROM arxiv_papers"))
    return int(res.scalar() or 0)


async def search_arxiv_papers(
    session: AsyncSession,
    *,
    keywords: list[str],
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    categories: list[str] | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """등록 키워드로 로컬 arxiv_papers FTS 검색 (rate limit 0).

    **키워드마다 개별 websearch_to_tsquery 서브쿼리 → UNION ALL → arxiv_id 로 dedup**
    (사용자 설계: 키워드별 검색 후 합집합). websearch_to_tsquery 가 다단어를 토큰 AND
    로 처리하므로 'Learned Point Cloud Compression' = 그 단어 모두 포함 논문. published
    DESC(최신 우선). 날짜/카테고리는 SQL WHERE(인덱스 활용).
    """
    kws = [k.strip() for k in keywords if k and k.strip()]
    if not kws:
        return []
    params: dict[str, Any] = {
        "lim": limit,
        "cats": categories or None,
        "date_from": date_from,
        "date_to": date_to,
    }
    subs = []
    for i, kw in enumerate(kws):
        params[f"q{i}"] = kw
        subs.append(f"""
            SELECT arxiv_id, title, abstract, authors, categories, published, version,
                   ts_rank(fts_vector, websearch_to_tsquery('simple', :q{i})) AS rank
            FROM arxiv_papers
            WHERE fts_vector @@ websearch_to_tsquery('simple', :q{i})
              AND (CAST(:cats AS TEXT[]) IS NULL OR categories && CAST(:cats AS TEXT[]))
              AND (CAST(:date_from AS TIMESTAMPTZ) IS NULL OR published >= CAST(:date_from AS TIMESTAMPTZ))
              AND (CAST(:date_to   AS TIMESTAMPTZ) IS NULL OR published <= CAST(:date_to   AS TIMESTAMPTZ))
        """)
    union_sql = " UNION ALL ".join(subs)
    sql = text(f"""
        SELECT arxiv_id, title, abstract, authors, categories, published, version,
               max(rank) AS rank
        FROM ( {union_sql} ) u
        GROUP BY arxiv_id, title, abstract, authors, categories, published, version
        ORDER BY published DESC NULLS LAST, rank DESC
        LIMIT :lim
    """)
    rows = (await session.execute(sql, params)).mappings().all()
    return [dict(r) for r in rows]
