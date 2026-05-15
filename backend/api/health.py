"""
헬스체크 — Postgres / Qdrant 연결 + 임베딩 차원 정보.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend import runtime_settings
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

    # LLM 의 effective provider/model 은 runtime_settings 가 가장 정확 (UI 의 DB 변경
    # 반영). env 의 settings.default_llm_provider 는 첫 부팅 시드값일 뿐 — health 는
    # '지금 어떤 provider/model 이 쓰이는지' 보고 싶은 게 의도라 effective 사용.
    health_info["config"] = {
        "embedding_backend": settings.embedding_backend,
        "embedding_model": settings.embedding_model,
        "default_llm_provider": runtime_settings.get_effective_llm_provider(),
        "ollama_model": runtime_settings.get_effective_ollama_model(),
    }
    return health_info
