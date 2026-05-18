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
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.connection import get_engine, get_session
from backend.db.repository import (
    get_item_full,
    update_item_read,
    update_item_user_notes,
)
from backend.llm.keyword_extract import extract_keywords_from_notes
from backend.schemas.models import (
    ItemAttachmentSummary,
    ItemDetail,
    ItemUpdateRequest,
)

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
