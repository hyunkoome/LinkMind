"""
헬스체크 — Postgres / Qdrant 연결 + 임베딩 차원 정보.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.db.connection import get_session
from backend.embedding.qdrant_store import get_qdrant_client

router = APIRouter()


@router.get("/health")
async def health(session: AsyncSession = Depends(get_session)) -> dict:
    settings = get_settings()
    health_info: dict = {"status": "ok", "components": {}}

    # Postgres
    try:
        await session.execute(text("SELECT 1"))
        health_info["components"]["postgres"] = "ok"
    except Exception as e:                       # noqa: BLE001 — heath 체크는 폭넓게
        health_info["components"]["postgres"] = f"error: {e!s}"
        health_info["status"] = "degraded"

    # Qdrant
    try:
        client = get_qdrant_client()
        cols = await client.get_collections()
        health_info["components"]["qdrant"] = {
            "ok": True,
            "collections": [c.name for c in cols.collections],
        }
    except Exception as e:                       # noqa: BLE001
        health_info["components"]["qdrant"] = f"error: {e!s}"
        health_info["status"] = "degraded"

    health_info["config"] = {
        "embedding_backend": settings.embedding_backend,
        "embedding_model": settings.embedding_model,
        "default_llm_provider": settings.default_llm_provider,
        "default_llm_model": settings.default_llm_model,
    }
    return health_info
