"""
GET /items/{id}, PATCH /items/{id}

item 의 전체 정보 조회 + user_notes / is_read 편집. Phase 2.5 — graph UI 의
modality viewer 가 이 endpoint 로 raw content + 메모 + 첨부를 한 번에 받아 표시.

설계 결정:
- raw_content 가 크다 (논문 PDF 수백 KB) — 일반 search 결과엔 포함 X, 여기만 반환.
- PATCH 는 partial update — user_notes / is_read 중 보낸 것만 반영. 둘 다 None
  이어도 200 (no-op) — UI 가 dirty 검사 없이 안전하게 호출 가능.
- user_notes 변경 시 → **BackgroundTask 로 LLM 키워드 추출 + items.tags 자동 병합**
  (한국어 자유 문체 지원). PATCH 응답은 즉시, 키워드 갱신은 백그라운드 (수십 초).
- LLM 호출 실패/타임아웃 시 tags 갱신만 skip — user_notes 자체는 이미 저장됨.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.connection import get_engine, get_session
from backend.db.repository import (
    append_item_user_notes,
    find_category_by_slug,
    get_item_full,
    link_topic_to_category,
    list_topics_for_item,
    update_item_read,
    update_item_user_notes,
)
from backend.llm.keyword_extract import extract_keywords_from_notes
from backend.schemas.models import (
    ItemAttachmentSummary,
    ItemDetail,
    ItemListCard,
    ItemListFacets,
    ItemListResponse,
    ItemUpdateRequest,
)
from pydantic import BaseModel, Field


class AppendNoteRequest(BaseModel):
    """POST /items/{id}/notes — user_notes 에 새 항목 append (덮어쓰기 X)."""
    note: str = Field(..., min_length=1, max_length=20000)


class LinkCategoryResponse(BaseModel):
    """POST /items/{id}/categories/{slug} — item 의 첫 topic 을 카테고리에 link."""
    linked: bool
    category_slug: str
    topic_id: UUID
    topic_slug: str

logger = logging.getLogger(__name__)
router = APIRouter()


_TAG_MAX = 32   # item 당 최대 tag (DB 인덱스 / UI 표시 제한)


def _to_attachment_summary(row: dict) -> ItemAttachmentSummary:
    return ItemAttachmentSummary(
        id=row["id"],
        role=row.get("role"),
        mime_type=row.get("mime_type"),
        file_size=row.get("file_size"),
        file_hash=row["file_hash"],
        caption=row.get("caption"),
        width=row.get("width"),
        height=row.get("height"),
    )


def _to_item_detail(row: dict) -> ItemDetail:
    attachments = [_to_attachment_summary(a) for a in (row.get("attachments") or [])]
    return ItemDetail(
        id=row["id"],
        source_type=row["source_type"],
        source_id=row.get("source_id"),
        source_url=row.get("source_url"),
        source_metadata=row.get("source_metadata") or {},
        title=row.get("title"),
        summary=row.get("summary"),
        raw_content=row.get("raw_content") or "",
        categories=list(row.get("categories") or []),
        tags=list(row.get("tags") or []),
        language=row.get("language"),
        source_created_at=row.get("source_created_at"),
        ingested_at=row["ingested_at"],
        updated_at=row["updated_at"],
        user_notes=row.get("user_notes"),
        user_notes_updated_at=row.get("user_notes_updated_at"),
        is_read=bool(row.get("is_read")),
        read_at=row.get("read_at"),
        attachments=attachments,
    )


def _merge_keep_order(existing: list[str], new: list[str], *, max_n: int = _TAG_MAX) -> list[str]:
    """기존 tags 우선 + 신규 키워드 append, 중복 제거, 최대 max_n 개."""
    merged: list[str] = []
    seen: set[str] = set()
    for t in (existing + new):
        if not t:
            continue
        key = t.lower()
        if key in seen:
            continue
        seen.add(key)
        merged.append(t)
        if len(merged) >= max_n:
            break
    return merged


def _truncate(s: str | None, n: int) -> str | None:
    if not s:
        return s
    s = s.strip()
    if len(s) <= n:
        return s
    return s[:n] + "…"


def _extract_domain(url: str | None) -> str | None:
    """source_url → hostname (www. 제외). 실패 시 None."""
    if not url or not url.startswith(("http://", "https://")):
        return None
    try:
        from urllib.parse import urlparse
        host = urlparse(url).hostname or None
        if host and host.startswith("www."):
            host = host[4:]
        return host
    except Exception:  # noqa: BLE001
        return None


# DB 쿼리에서 domain 추출 — Python urlparse 와 일치. 'www.' 제거.
# 주의: SQLAlchemy text() 가 `:name` 을 named param 으로 잡으므로 정규식의 `?:`
# non-capturing group 은 못 씀. capturing group `(www\.)?` 으로 우회 (regexp_replace
# 는 첫 매치 전체를 대체하므로 결과 동일).
_DOMAIN_EXPR = (
    "lower(regexp_replace("
    "regexp_replace(source_url, '^https?://(www\\.)?', ''),"
    "'/.*$', ''))"
)


def _build_where(
    *,
    kind: str | None,
    source_type: str | None,
    domain: str | None,
    has_user_notes: bool | None,
    has_summary: bool | None,
    q: str | None,
    skip_kind: bool = False,
    skip_source_type: bool = False,
    skip_domain: bool = False,
) -> tuple[list[str], dict[str, Any]]:
    """동적 WHERE 절 빌더 — 적용 필터별 (?: param) 절 + param dict.

    skip_* 는 facet 쿼리용 — 자기 자신 facet 은 필터링 제외 (drilldown UX).
    """
    where: list[str] = []
    params: dict[str, Any] = {}

    if kind and not skip_kind:
        if kind == "none":
            # cleanup 필요 없는 정상 자료
            where.append("NOT (source_metadata::jsonb ? 'fetch_error_kind')")
        else:
            where.append("source_metadata::jsonb->>'fetch_error_kind' = :kind")
            params["kind"] = kind
    if source_type and not skip_source_type:
        where.append("source_type = :source_type")
        params["source_type"] = source_type
    if domain and not skip_domain:
        where.append(f"{_DOMAIN_EXPR} = :domain")
        params["domain"] = domain.lower()
    if has_user_notes is True:
        where.append("user_notes IS NOT NULL AND user_notes <> ''")
    elif has_user_notes is False:
        where.append("(user_notes IS NULL OR user_notes = '')")
    if has_summary is True:
        where.append("summary IS NOT NULL AND summary <> ''")
    elif has_summary is False:
        where.append("(summary IS NULL OR summary = '')")
    if q and q.strip():
        # 제목 / raw_content / user_notes 부분일치 (ILIKE — GIN trgm 없으면 seq scan).
        # MVP 라 작은 데이터셋 (~20K row) 에서 충분.
        where.append(
            "(title ILIKE :q OR raw_content ILIKE :q OR user_notes ILIKE :q)"
        )
        params["q"] = f"%{q.strip()}%"

    return where, params


async def _fetch_facet(
    session: AsyncSession, *, expr: str, where_clauses: list[str],
    params: dict[str, Any], limit: int = 30,
) -> dict[str, int]:
    """단일 facet aggregation — expr 별 count 상위 limit 개."""
    where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    sql = f"""
        SELECT {expr} AS key, COUNT(*) AS n
        FROM items
        {where_sql}
        GROUP BY key
        ORDER BY n DESC, key
        LIMIT :facet_limit
    """
    res = await session.execute(sql_text(sql), {**params, "facet_limit": limit})
    out: dict[str, int] = {}
    for r in res.mappings().all():
        k = r["key"]
        if k is None or (isinstance(k, str) and k == ""):
            continue
        out[str(k)] = int(r["n"])
    return out


@router.get("", response_model=ItemListResponse)
async def list_items(
    kind: str | None = None,            # image_no_ocr | extraction_failed | binary_no_extract | short_raw | none
    source_type: str | None = None,
    domain: str | None = None,
    has_user_notes: bool | None = None,
    has_summary: bool | None = None,
    q: str | None = None,
    sort: str = "ingested_at_desc",     # ingested_at_desc | ingested_at_asc | domain | kind
    page: int = 1,
    page_size: int = 50,
    session: AsyncSession = Depends(get_session),
) -> ItemListResponse:
    """D12 cleanup 페이지용 list endpoint.

    필터 + pagination + facets 한 번에. 비싸지 않은 query (count + main + 3 facet,
    각각 source_metadata GIN 인덱스가 없어 seq scan 이지만 16K row 정도면 < 100ms).
    """
    if page < 1:
        page = 1
    if page_size < 1 or page_size > 200:
        page_size = 50

    where_clauses, params = _build_where(
        kind=kind, source_type=source_type, domain=domain,
        has_user_notes=has_user_notes, has_summary=has_summary, q=q,
    )
    where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    # ORDER BY
    if sort == "ingested_at_asc":
        order_sql = "ORDER BY ingested_at ASC, id ASC"
    elif sort == "domain":
        order_sql = f"ORDER BY {_DOMAIN_EXPR} NULLS LAST, ingested_at DESC"
    elif sort == "kind":
        order_sql = "ORDER BY source_metadata::jsonb->>'fetch_error_kind' NULLS LAST, ingested_at DESC"
    else:
        order_sql = "ORDER BY ingested_at DESC, id DESC"

    # main query — raw_content 는 LEFT(...) 로 잘라서 가져옴 (큰 PDF 본문 전체 보내지 않음).
    offset = (page - 1) * page_size
    main_sql = f"""
        SELECT
          id, source_type, source_url, title,
          LEFT(COALESCE(summary, ''), 240)  AS summary_preview,
          LEFT(COALESCE(raw_content, ''), 400) AS raw_preview,
          length(raw_content) AS raw_length,
          {_DOMAIN_EXPR} AS domain,
          source_metadata::jsonb->>'fetch_error_kind' AS fetch_error_kind,
          source_metadata::jsonb->>'fetch_error'      AS fetch_error_message,
          (user_notes IS NOT NULL AND user_notes <> '') AS has_user_notes,
          LEFT(COALESCE(user_notes, ''), 200) AS user_notes_preview,
          tags, is_read, ingested_at
        FROM items
        {where_sql}
        {order_sql}
        LIMIT :limit OFFSET :offset
    """
    res = await session.execute(
        sql_text(main_sql),
        {**params, "limit": page_size, "offset": offset},
    )
    rows = res.mappings().all()

    # COUNT
    count_sql = f"SELECT COUNT(*) AS n FROM items {where_sql}"
    cres = await session.execute(sql_text(count_sql), params)
    total = int(cres.scalar_one())

    # attachments 동봉 — 카드 한 건당 image_no_ocr 의 이미지 표시용 (file_hash).
    item_ids = [r["id"] for r in rows]
    attach_map: dict[UUID, list[ItemAttachmentSummary]] = {iid: [] for iid in item_ids}
    if item_ids:
        ares = await session.execute(
            sql_text("""
                SELECT id, item_id, role, mime_type, file_size, file_hash, caption, width, height
                FROM attachments
                WHERE item_id = ANY(:ids)
                ORDER BY item_id, id
            """),
            {"ids": item_ids},
        )
        for ar in ares.mappings().all():
            attach_map.setdefault(ar["item_id"], []).append(
                _to_attachment_summary(dict(ar))
            )

    cards = [
        ItemListCard(
            id=r["id"],
            source_type=r["source_type"],
            source_url=r["source_url"],
            title=r["title"],
            summary_preview=_truncate(r["summary_preview"], 240) or None,
            raw_preview=_truncate(r["raw_preview"], 400) or None,
            raw_length=int(r["raw_length"] or 0),
            domain=r["domain"],
            fetch_error_kind=r["fetch_error_kind"],
            fetch_error_message=r["fetch_error_message"],
            has_user_notes=bool(r["has_user_notes"]),
            user_notes_preview=r["user_notes_preview"] or None,
            tags=list(r["tags"] or []),
            is_read=bool(r["is_read"]),
            ingested_at=r["ingested_at"],
            attachments=attach_map.get(r["id"], []),
        )
        for r in rows
    ]

    # Facets — drilldown UX 위해 자기 자신 facet 은 필터링 제외.
    # 예: kind=image_no_ocr 적용 중이어도 kind facet 은 전체 (다른 kind 도 보임).
    facet_kind_where, facet_kind_params = _build_where(
        kind=kind, source_type=source_type, domain=domain,
        has_user_notes=has_user_notes, has_summary=has_summary, q=q,
        skip_kind=True,
    )
    facet_st_where, facet_st_params = _build_where(
        kind=kind, source_type=source_type, domain=domain,
        has_user_notes=has_user_notes, has_summary=has_summary, q=q,
        skip_source_type=True,
    )
    facet_dom_where, facet_dom_params = _build_where(
        kind=kind, source_type=source_type, domain=domain,
        has_user_notes=has_user_notes, has_summary=has_summary, q=q,
        skip_domain=True,
    )

    facet_kind = await _fetch_facet(
        session,
        expr="COALESCE(source_metadata::jsonb->>'fetch_error_kind', 'none')",
        where_clauses=facet_kind_where, params=facet_kind_params, limit=10,
    )
    facet_st = await _fetch_facet(
        session, expr="source_type",
        where_clauses=facet_st_where, params=facet_st_params, limit=20,
    )
    facet_dom = await _fetch_facet(
        session, expr=_DOMAIN_EXPR,
        where_clauses=facet_dom_where, params=facet_dom_params, limit=30,
    )

    return ItemListResponse(
        items=cards,
        total=total,
        page=page,
        page_size=page_size,
        facets=ItemListFacets(
            kind=facet_kind, source_type=facet_st, domain=facet_dom,
        ),
    )


@router.get("/{item_id}", response_model=ItemDetail)
async def get_item(
    item_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> ItemDetail:
    row = await get_item_full(session, item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="item not found")
    return _to_item_detail(row)


@router.patch("/{item_id}", response_model=ItemDetail)
async def patch_item(
    item_id: UUID,
    body: ItemUpdateRequest,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> ItemDetail:
    """user_notes / is_read 편집 (partial update).

    user_notes 변경 시 BackgroundTask 가 LLM 으로 키워드 추출 + items.tags 병합.
    PATCH 응답은 즉시 (현재 user_notes/is_read 만 반영), tags 는 백그라운드 갱신.
    """
    exists = await get_item_full(session, item_id)
    if exists is None:
        raise HTTPException(status_code=404, detail="item not found")

    notes_changed = False
    read_changed = False

    if body.user_notes is not None:
        notes_changed = await update_item_user_notes(
            session, item_id=item_id, user_notes=body.user_notes,
        )

    if body.is_read is not None:
        read_changed = await update_item_read(
            session, item_id=item_id, is_read=body.is_read,
        )

    if notes_changed or read_changed:
        await session.commit()

    # user_notes 가 실제로 바뀌었을 때만 LLM 키워드 추출 background task 예약.
    # 빈 메모 (None / "") 로 갱신된 경우는 호출 skip (extract_keywords_from_notes
    # 가 짧은 입력 거름).
    if notes_changed and body.user_notes:
        background.add_task(_extract_and_merge_tags, str(item_id), body.user_notes)

    fresh = await get_item_full(session, item_id)
    if fresh is None:  # pragma: no cover — patch 후 동시 삭제 race
        raise HTTPException(status_code=404, detail="item disappeared")
    return _to_item_detail(fresh)


@router.post("/{item_id}/notes", response_model=ItemDetail)
async def append_note(
    item_id: UUID,
    body: AppendNoteRequest,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> ItemDetail:
    """user_notes 에 새 항목 append (기존 메모 보존, timestamp 구분자 자동).

    D12 cleanup 페이지에서 사용자가 "본문 직접 붙여넣기" / "메모 추가" 시 호출.
    PATCH /items/{id} 의 user_notes set 의미와 분리 — 학습 데이터 보전 (§2)
    상 누적 메모는 절대 덮어쓰지 않는다.

    동작:
      - 빈 note 거름 (Pydantic min_length=1)
      - 기존 user_notes 와 같은 내용이면 idempotent (no-op)
      - 비어있던 메모면 그대로 set (timestamp 구분자 없이)
      - 그 외에는 `<기존>\n\n--- YYYY-MM-DD HH:MM ---\n<new>` append
      - 변경 시 LLM 키워드 추출 background task 예약 (기존 PATCH 와 동일)
    """
    exists = await get_item_full(session, item_id)
    if exists is None:
        raise HTTPException(status_code=404, detail="item not found")

    changed = await append_item_user_notes(
        session, item_id=item_id, new_note=body.note,
    )
    if changed:
        await session.commit()
        # 추가된 note 의 키워드 추출 → tags 병합 (whole user_notes 가 아닌 new 부분만 분석)
        background.add_task(_extract_and_merge_tags, str(item_id), body.note)

    fresh = await get_item_full(session, item_id)
    if fresh is None:  # pragma: no cover
        raise HTTPException(status_code=404, detail="item disappeared")
    return _to_item_detail(fresh)


@router.post("/{item_id}/categories/{slug}", response_model=LinkCategoryResponse)
async def link_item_category(
    item_id: UUID,
    slug: str,
    session: AsyncSession = Depends(get_session),
) -> LinkCategoryResponse:
    """item 의 첫 topic 을 카테고리에 수동 link (source='manual').

    카테고리는 topic 에 붙고 item 에 직접 안 붙는 wave-4 구조. cleanup UI 에서
    "이 자료를 카테고리 X 로" 액션 시, item 의 첫 topic (auto_link 가 ingest 시
    fallback topic 자동 생성하므로 거의 항상 존재) 을 그 카테고리에 link.

    item 에 topic 이 하나도 없으면 400 (현 데이터셋에선 거의 없는 케이스).
    """
    exists = await get_item_full(session, item_id)
    if exists is None:
        raise HTTPException(status_code=404, detail="item not found")

    cat = await find_category_by_slug(session, slug=slug)
    if not cat:
        raise HTTPException(404, f"category 없음: {slug}")

    topics = await list_topics_for_item(session, item_id=item_id)
    if not topics:
        raise HTTPException(
            400,
            "item 에 연결된 topic 이 없습니다. 카테고리 link 가 불가능합니다."
            " (ingest 흐름이 fallback topic 자동 생성 — 옛 데이터일 가능성)",
        )

    topic = topics[0]
    changed = await link_topic_to_category(
        session, topic_id=topic["id"], category_id=cat["id"], source="manual",
    )
    if changed:
        await session.commit()
    return LinkCategoryResponse(
        linked=changed,
        category_slug=slug,
        topic_id=topic["id"],
        topic_slug=topic["slug"],
    )


@router.delete("/{item_id}")
async def delete_item(
    item_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """item 영구 삭제 (irreversible).

    D12 cleanup 페이지에서 진짜 사라진 자료 (YouTube 영상 삭제 / 도메인 죽음 등)
    를 사용자 명시적 의사로 정리할 때. §11 Privacy 원칙 §4 (삭제 권리, GDPR/PIPA)
    에 부합 — §2 raw-first 원칙은 "ingest 시점 raw 무손실 보존" 의미라 사용자
    명시적 삭제와 충돌 X.

    삭제 흐름:
      1. Qdrant chunks collection 의 item_id payload 별 points 삭제
      2. Postgres items row DELETE
         → ON DELETE CASCADE 로 chunks / attachments / item_topics 자동 삭제

    보존되는 것:
      - volumes/archive 의 raw 파일 (attachments.file_hash) — SHA-256 dedup 라
        다른 item 이 같은 file_hash 참조 가능. orphan cleanup 은 별도 job.

    Returns: {deleted: bool, item_id, qdrant_status}.
      - 존재 안 하면 404.
    """
    exists = await get_item_full(session, item_id)
    if exists is None:
        raise HTTPException(status_code=404, detail="item not found")

    # 1. Qdrant points 삭제 (Postgres CASCADE 와 별개 — 분리 시스템)
    from backend.embedding.qdrant_store import delete_chunks_for_item
    qdrant_status = await delete_chunks_for_item(str(item_id))

    # 2. Postgres DELETE — CASCADE 가 자동 처리
    await session.execute(
        sql_text("DELETE FROM items WHERE id = :id"),
        {"id": item_id},
    )
    await session.commit()

    logger.info(
        "item 삭제 완료 — id=%s, source_type=%s, qdrant_status=%d",
        item_id, exists.get("source_type"), qdrant_status,
    )
    return {
        "deleted": True,
        "item_id": str(item_id),
        "qdrant_status": qdrant_status,
    }


async def _extract_and_merge_tags(item_id_str: str, user_notes: str) -> None:
    """BackgroundTask — LLM 키워드 추출 + tags 병합 (별도 DB session).

    BackgroundTasks 가 호출하는 함수는 request session 과 분리되어야 (요청
    응답 후에도 실행). engine 의 sessionmaker 에서 새 session.
    """
    try:
        keywords = await extract_keywords_from_notes(user_notes)
    except Exception as e:  # noqa: BLE001
        logger.warning("background 키워드 추출 실패 (item=%s): %s", item_id_str, e)
        return
    if not keywords:
        return

    item_id = UUID(item_id_str)

    # 별도 session — engine 의 sessionmaker 가져옴
    engine = get_engine()
    from sqlalchemy.ext.asyncio import async_sessionmaker
    SessionMaker = async_sessionmaker(engine, expire_on_commit=False)

    async with SessionMaker() as session:
        async with session.begin():
            res = await session.execute(
                sql_text("SELECT tags FROM items WHERE id = :id"),
                {"id": item_id},
            )
            row = res.mappings().one_or_none()
            if row is None:
                logger.info("background 키워드 병합 — item 사라짐 (id=%s)", item_id)
                return
            existing = list(row.get("tags") or [])
            merged = _merge_keep_order(existing, keywords, max_n=_TAG_MAX)
            if merged == existing:
                logger.info("background 키워드 병합 — 추가 없음 (item=%s)", item_id)
                return
            await session.execute(
                sql_text("UPDATE items SET tags = :tags WHERE id = :id"),
                {"id": item_id, "tags": merged},
            )
    logger.info(
        "background 키워드 병합 완료 — item=%s, 추가 %d → 총 %d",
        item_id, len(merged) - len(existing), len(merged),
    )
