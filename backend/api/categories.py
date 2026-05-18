"""
GET  /categories            — 전체 카테고리 (topic/item count 포함)
GET  /categories/{slug}     — 카테고리 + 속한 topics
POST /categories            — 카테고리 신규/갱신 (upsert by slug)
POST /categories/{slug}/topics/{topic_id} — 수동 link (source='manual')

키워드 카테고리 노드 계층 (Phase 2.5 wave-3). items.tags 의 raw 해시태그를
정규화한 layer — 같은 의미 다른 표기 (`#3DGS`/`#gaussian-splatting`) 를
하나의 category 로 통합.
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.connection import get_session
from backend.db.repository import (
    find_category_by_slug,
    link_topic_to_category,
    list_categories,
    list_topics_in_category,
    upsert_category,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ──────────────────────────────────────────────────────────────
# Schemas
# ──────────────────────────────────────────────────────────────


class CategorySummary(BaseModel):
    id: UUID
    slug: str
    label: str
    description: str | None = None
    synonyms: list[str] = Field(default_factory=list)
    color: str | None = None
    pinned: bool = False
    topic_count: int = 0
    item_count: int = 0


class CategoryTopic(BaseModel):
    """카테고리에 속한 topic — graph expand 시 노출."""
    id: UUID
    slug: str
    title: str
    primary_external_id: dict | None = None
    tags: list[str] = Field(default_factory=list)
    item_count: int = 0


class CategoryDetail(BaseModel):
    id: UUID
    slug: str
    label: str
    description: str | None = None
    synonyms: list[str] = Field(default_factory=list)
    color: str | None = None
    pinned: bool = False
    topics: list[CategoryTopic] = Field(default_factory=list)


class CategoryUpsert(BaseModel):
    slug: str = Field(..., min_length=1, max_length=120)
    label: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    synonyms: list[str] = Field(default_factory=list)
    color: str | None = Field(None, description="HEX 색상 (예: #ff8800)")
    pinned: bool = False


# ──────────────────────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────────────────────


@router.get("", response_model=list[CategorySummary])
async def get_categories(
    limit: int = 1000,
    session: AsyncSession = Depends(get_session),
) -> list[CategorySummary]:
    rows = await list_categories(session, limit=limit)
    return [CategorySummary(**r) for r in rows]


@router.get("/{slug}", response_model=CategoryDetail)
async def get_category(
    slug: str,
    session: AsyncSession = Depends(get_session),
) -> CategoryDetail:
    cat = await find_category_by_slug(session, slug=slug)
    if not cat:
        raise HTTPException(404, f"category 없음: {slug}")
    topics = await list_topics_in_category(session, category_id=cat["id"])
    return CategoryDetail(
        **cat,
        topics=[CategoryTopic(**t) for t in topics],
    )


@router.post("", response_model=CategorySummary)
async def upsert_category_endpoint(
    payload: CategoryUpsert,
    session: AsyncSession = Depends(get_session),
) -> CategorySummary:
    """category INSERT or UPDATE (slug UNIQUE 기준)."""
    cid = await upsert_category(
        session,
        slug=payload.slug,
        label=payload.label,
        description=payload.description,
        synonyms=payload.synonyms,
        color=payload.color,
        pinned=payload.pinned,
    )
    await session.commit()
    # 갱신 후 fresh row (synonyms union 반영) + count 조회
    cat = await find_category_by_slug(session, slug=payload.slug)
    if not cat:
        raise HTTPException(500, "upsert 후 카테고리 조회 실패")
    # find_category_by_slug 가 이제 topic_count / item_count 도 동봉.
    return CategorySummary(**cat)


@router.post("/{slug}/topics/{topic_id}")
async def link_topic_manual(
    slug: str,
    topic_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """카테고리 ↔ topic 수동 link (source='manual' — auto 보다 우선)."""
    cat = await find_category_by_slug(session, slug=slug)
    if not cat:
        raise HTTPException(404, f"category 없음: {slug}")
    changed = await link_topic_to_category(
        session, topic_id=topic_id, category_id=cat["id"], source="manual",
    )
    await session.commit()
    return {"linked": changed, "category_slug": slug, "topic_id": str(topic_id)}
