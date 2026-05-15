"""
GET /topics, GET /topics/{id}, GET /items/{id}/topics, POST /items/{id}/topics

자동 그룹핑된 topic 의 조회 + 수동 link/unlink.
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.connection import get_session
from backend.db.repository import (
    find_topic_by_slug,
    link_item_to_topic,
    list_items_for_topic,
    list_topics,
    list_topics_for_item,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ──────────────────────────────────────────────────────────────
# Schemas
# ──────────────────────────────────────────────────────────────


class TopicSummary(BaseModel):
    """목록 / 검색 결과용 — item_count 포함."""
    id: UUID
    slug: str
    title: str
    primary_external_id: dict | None = None
    tags: list[str] = Field(default_factory=list)
    item_count: int = 0


class TopicItem(BaseModel):
    """topic 안의 한 item — 모달리티(role) 정보."""
    id: UUID
    source_type: str
    source_url: str | None
    title: str | None
    summary: str | None
    tags: list[str] = Field(default_factory=list)
    role: str
    confidence: float
    source: str
    note: str | None = None


class TopicDetail(BaseModel):
    id: UUID
    slug: str
    title: str
    description: str | None
    primary_external_id: dict | None
    tags: list[str] = Field(default_factory=list)
    items: list[TopicItem] = Field(default_factory=list)


class ItemTopic(BaseModel):
    """item 의 topic membership — 한 item 이 어떤 topic 들에 묶여 있는지."""
    id: UUID
    slug: str
    title: str
    primary_external_id: dict | None = None
    tags: list[str] = Field(default_factory=list)
    role: str
    confidence: float
    source: str


class LinkRequest(BaseModel):
    topic_slug: str = Field(..., min_length=1, description="link 할 대상 topic 의 slug")
    role: str = Field(..., description="paper/code/video/playlist/pdf/blog/note")
    note: str | None = None


# ──────────────────────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────────────────────


@router.get("", response_model=list[TopicSummary])
async def list_topics_endpoint(
    limit: int = 50,
    session: AsyncSession = Depends(get_session),
) -> list[TopicSummary]:
    """최신 updated 순으로 topic 목록 + 각 topic 의 item 수."""
    rows = await list_topics(session, limit=limit)
    return [TopicSummary(**row) for row in rows]


# items/ 류는 path catch-all 보다 먼저 정의 — 그래야 '/topics/items/...' 가
# '/topics/{slug:path}' 에 잡혀 들어가지 않음 (FastAPI 는 등록 순서 매칭).
@router.get("/items/{item_id}", response_model=list[ItemTopic])
async def topics_for_item(
    item_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> list[ItemTopic]:
    """이 item 이 묶인 모든 topic — Streamlit 검색 결과 / item 상세 에서 활용."""
    rows = await list_topics_for_item(session, item_id=item_id)
    return [ItemTopic(**r) for r in rows]


@router.post("/items/{item_id}/link", response_model=ItemTopic)
async def link_item_topic_manual(
    item_id: UUID,
    payload: LinkRequest,
    session: AsyncSession = Depends(get_session),
) -> ItemTopic:
    """수동 link — 자동 그룹핑이 놓친 케이스. source='manual' 로 저장돼 auto 가
    이후 덮어쓰지 못함 (repository.link_item_to_topic 의 UPSERT 정책 참고)."""
    topic = await _resolve_topic(session, payload.topic_slug)
    await link_item_to_topic(
        session,
        item_id=item_id,
        topic_id=topic["id"],
        role=payload.role,
        confidence=1.0,
        source="manual",
        note=payload.note,
    )
    await session.commit()
    return ItemTopic(
        id=topic["id"], slug=topic["slug"], title=topic["title"],
        primary_external_id=topic.get("primary_external_id"),
        tags=topic.get("tags") or [],
        role=payload.role, confidence=1.0, source="manual",
    )


# `:path` 컨버터 — slug 안에 '/' 가 들어가는 경우 (예: 'github:owner/repo') 도
# 그대로 받음. items/ 류 보다 *뒤* 에 정의해야 fall-through 가 의도대로.
@router.get("/{topic_id_or_slug:path}", response_model=TopicDetail)
async def get_topic(
    topic_id_or_slug: str,
    session: AsyncSession = Depends(get_session),
) -> TopicDetail:
    """topic 상세 + 그 안의 모든 item (role 정렬). UUID 또는 slug 둘 다 허용."""
    topic = await _resolve_topic(session, topic_id_or_slug)
    items = await list_items_for_topic(session, topic_id=topic["id"])
    return TopicDetail(
        **{k: topic[k] for k in (
            "id", "slug", "title", "description", "primary_external_id", "tags",
        )},
        items=[TopicItem(**i) for i in items],
    )


# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────


async def _resolve_topic(session: AsyncSession, key: str) -> dict:
    """key 가 UUID 형식이면 id, 아니면 slug 로 조회 — UI 친화 (slug 가 더 외우기 쉬움)."""
    try:
        tid = UUID(key)
        from sqlalchemy import text
        res = await session.execute(
            text("""
                SELECT id, slug, title, description, primary_external_id, tags
                FROM topics WHERE id = :id
            """),
            {"id": str(tid)},
        )
        row = res.mappings().one_or_none()
        if row:
            return dict(row)
    except ValueError:
        pass

    topic = await find_topic_by_slug(session, key)
    if topic is None:
        raise HTTPException(status_code=404, detail=f"topic 없음: {key}")
    return topic
