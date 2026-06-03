"""
Postgres 비동기 연결 (SQLAlchemy 2.0 + asyncpg).

엔진은 프로세스 단위 싱글톤. FastAPI 의존성 주입은 `get_session`을 사용.
"""

from __future__ import annotations

import logging
from typing import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from starlette.requests import Request

from backend.config import get_settings

logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine, _session_factory
    if _engine is None:
        settings = get_settings()
        url = settings.effective_database_url
        logger.info("DB 엔진 생성: %s", url.rsplit("@", 1)[-1])
        _engine = create_async_engine(
            url,
            echo=False,
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=20,
        )
        _session_factory = async_sessionmaker(
            _engine, expire_on_commit=False, class_=AsyncSession
        )
    return _engine


async def get_session(request: Request = None) -> AsyncIterator[AsyncSession]:  # type: ignore[assignment]
    """FastAPI 의존성 — 요청 1건당 세션 1개.

    멀티테넌트(2026-06-03): request.state.auth['space'] 가 있으면 Postgres RLS 변수
    `app.current_space_id` 를 트랜잭션 스코프(set_config local=true)로 설정한다. 단계 C 에서
    각 테이블 RLS 정책이 이 값을 읽어 cross-space 유출을 DB 레벨에서 차단한다. 정책이 아직
    없는 단계 A/B 에서는 변수만 설정되고 효과는 없음 (무해).

    request 는 FastAPI 가 자동 주입 (타입이 Request). 비-FastAPI 호출은 get_session_factory 사용.
    """
    if _session_factory is None:
        get_engine()
    assert _session_factory is not None
    space_id: str | None = None
    if request is not None:
        auth = getattr(request.state, "auth", None)
        if auth and auth.get("space"):
            space_id = str(auth["space"])
    async with _session_factory() as session:
        if space_id is not None:
            await session.execute(
                text("SELECT set_config('app.current_space_id', :sid, true)"),
                {"sid": space_id},
            )
        yield session


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """async session factory 반환 — FastAPI 밖 (jobs / smoke / BackgroundTask) 용."""
    if _session_factory is None:
        get_engine()
    assert _session_factory is not None
    return _session_factory


async def close_engine() -> None:
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None
