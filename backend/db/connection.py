"""
Postgres 비동기 연결 (SQLAlchemy 2.0 + asyncpg).

엔진은 프로세스 단위 싱글톤. FastAPI 의존성 주입은 `get_session`을 사용.
"""

from __future__ import annotations

import logging
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

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


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 의존성 — 요청 1건당 세션 1개."""
    if _session_factory is None:
        get_engine()
    assert _session_factory is not None
    async with _session_factory() as session:
        yield session


async def close_engine() -> None:
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None
