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

import asyncio

from backend import runtime_settings
from backend.api import (
    ask,
    categories,
    files,
    graph,
    health,
    ingest,
    items,
    search,
    settings as settings_api,
    topics,
)
from backend.config import get_settings
from backend.db.connection import close_engine, get_engine
from backend.jobs.analysis_worker import run_analysis_worker

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
    # runtime_settings 시드(없으면 v1 prompt 등록) + DB → in-memory 캐시 적재.
    # DB 가 죽었으면 여기서 raise 되어 startup 실패 — health-degraded 보다 빠른 신호.
    try:
        await runtime_settings.seed_and_load()
    except Exception as e:  # noqa: BLE001
        # DB 가 잠시 불안한 상태일 수도 있으니 startup 자체는 막지 않음. 첫 요청 시
        # get_active_prompt 가 seed-fallback 으로 동작.
        logger.error("runtime_settings 적재 실패 — env/코드 시드로 fallback: %s", e)

    # analysis_worker — 백그라운드 task. ingest 시 summarize=False 로 빠르게 들어온
    # item 의 chunks (embedding) + summary (LLM) 를 천천히 채움.
    # 사용자 architecture 비판 반영 (2026-05-18): 텔레그램 ingest 가 LLM 호출까지
    # 동기로 하면 1메시지 ~30-60초 → 채널 비우는 데 사용자 막힘.
    # → 텔레그램 daemon 은 raw + 채널 삭제만 즉시, 이 worker 가 deferred 분석.
    worker_stop = asyncio.Event()
    worker_task = asyncio.create_task(
        run_analysis_worker(stop_event=worker_stop),
        name="analysis_worker",
    )

    try:
        yield
    finally:
        logger.info("LinkMind 종료 — analysis_worker 정리 + DB 엔진 close")
        worker_stop.set()
        # LLM 호출 중일 수 있어 60초까지 graceful 대기. 그 후 force cancel.
        # (cancel 시 in-flight DB transaction 은 rollback 됨 — 다음 시작 시 그 item 재처리)
        try:
            await asyncio.wait_for(worker_task, timeout=60.0)
        except asyncio.TimeoutError:
            logger.warning("analysis_worker timeout (60s) — force cancel")
            worker_task.cancel()
            try:
                await worker_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        except Exception as e:  # noqa: BLE001
            logger.warning("analysis_worker 종료 중 예외: %s", e)
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
app.include_router(settings_api.router, prefix="/settings", tags=["settings"])
app.include_router(files.router, prefix="/files", tags=["files"])
app.include_router(topics.router, prefix="/topics", tags=["topics"])
app.include_router(items.router, prefix="/items", tags=["items"])
app.include_router(graph.router, prefix="/graph", tags=["graph"])
app.include_router(categories.router, prefix="/categories", tags=["categories"])


@app.get("/")
async def root() -> dict:
    return {
        "name": "LinkMind",
        "version": app.version,
        "docs": "/docs",
        "purpose": "raw-first knowledge OS for personal sVLL training",
    }
