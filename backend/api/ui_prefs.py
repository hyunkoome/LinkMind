"""
backend.api.ui_prefs — 유저별 UI 설정 키-값 (패널 폭 등, 2026-06-05).

페이지별 레이아웃(예: arxiv 3-패널 폭)을 DB 에 저장해 다른 기기/재방문에도 유지.
본인 것만 접근(get_current_user). pref_key 는 페이지 prefix 포함(예: 'arxiv:leftW').
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_user
from backend.db import repository
from backend.db.connection import get_session

router = APIRouter()


class UiPrefSet(BaseModel):
    key: str = Field(..., min_length=1, max_length=200)
    value: str = Field(..., max_length=2000)


@router.get("")
async def get_prefs(
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """본인 UI 설정 전체 {key: value}."""
    prefs = await repository.get_user_ui_prefs(session, user_id=UUID(str(user["id"])))
    return {"prefs": prefs}


@router.put("")
async def set_pref(
    payload: UiPrefSet,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """UI 설정 한 항목 저장(upsert)."""
    await repository.set_user_ui_pref(
        session, user_id=UUID(str(user["id"])), key=payload.key, value=payload.value,
    )
    await session.commit()
    return {"ok": True}
