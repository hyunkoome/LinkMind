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
# helpers
# ──────────────────────────────────────────────────────────────


def _to_json(d: dict[str, Any]) -> str:
    """psycopg/asyncpg에 JSONB로 전달하기 위한 직렬화."""
    import orjson
    return orjson.dumps(d).decode("utf-8")
