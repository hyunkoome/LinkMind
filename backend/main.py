"""
LinkMind FastAPI 진입점.

실행:
    cd <project_root>
    uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api import ask, health, ingest, search
from backend.config import get_settings
from backend.db.connection import close_engine, get_engine

settings = get_settings()

logging.basicConfig(
    level=settings.linkmind_log_level,
    format="%(asctime)s %(levelname)-8s %(name)s : %(message)s",
)
logger = logging.getLogger("linkmind")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """FastAPI lifespan — 시작/종료 훅."""
    logger.info("LinkMind 시작 (env: %s, DB: %s)", "docker" if settings.linkmind_host == "0.0.0.0" else "local",
                settings.effective_database_url.split("@")[-1])  # 비밀번호 제외
    # DB 엔진 미리 워밍업 (실패하면 즉시 알 수 있도록)
    _ = get_engine()
    yield
    logger.info("LinkMind 종료 — 리소스 정리")
    await close_engine()


app = FastAPI(
    title="LinkMind API",
    description="개인 AI Research OS — 데이터 수집/분석/검색 + 학습 데이터 export",
    version="0.1.0",
    lifespan=lifespan,
)

# 개발 편의를 위한 CORS — 운영 시 origins 제한 필요
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ──────────────────────────────────────────────────────
app.include_router(health.router, tags=["health"])
app.include_router(ingest.router, prefix="/ingest", tags=["ingest"])
app.include_router(search.router, prefix="/search", tags=["search"])
app.include_router(ask.router, prefix="/ask", tags=["ask"])


@app.get("/")
async def root() -> dict:
    return {
        "name": "LinkMind",
        "version": app.version,
        "docs": "/docs",
        "purpose": "raw-first knowledge OS for personal sVLL training",
    }
