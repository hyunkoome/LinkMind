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

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

import asyncio

from backend import runtime_settings
from backend.api import (
    admin_arxiv,
    ask,
    auth as auth_api,
    files,
    graph,
    health,
    ingest,
    items,
    search,
    sessions as sessions_api,
    settings as settings_api,
    topics,
    wiki,
)
from backend.api.deps import get_current_user
from backend.api.middleware import AuthMiddleware
from backend.config import get_settings
from backend.db.connection import close_engine, get_engine
from backend.jobs.analysis_worker import run_analysis_worker
from backend.jobs.wiki_writer_worker import run_wiki_writer_worker

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

    # 멀티테넌트(2026-06-03) — 첫 관리자/조직은 자동 seed 하지 않는다. 설치 후 user 0명이면
    # POST /auth/bootstrap (브라우저 /login '조직 만들기')으로 고객 조직이 직접 첫 관리자를
    # 만든다. 운영자는 인프라만 제공 (데이터 접근 X). env seed 자동생성 제거.

    # analysis_worker — 백그라운드 task. ingest 시 summarize=False 로 빠르게 들어온
    # item 의 chunks (embedding) + summary (LLM) 를 천천히 채움.
    # 사용자 architecture 비판 반영 (2026-05-18): 텔레그램 ingest 가 LLM 호출까지
    # 동기로 하면 1메시지 ~30-60초 → 채널 비우는 데 사용자 막힘.
    # → 텔레그램 daemon 은 raw + 채널 삭제만 즉시, 이 worker 가 deferred 분석.
    # → 그 끝에 wave-2c: classifier hook (item → wiki_pages 자동 분류, M:N).
    worker_stop = asyncio.Event()
    worker_task = asyncio.create_task(
        run_analysis_worker(stop_event=worker_stop),
        name="analysis_worker",
    )

    # wiki_writer_worker — D10 wave-2 (2026-05-26 사용자 명시 정책).
    # 분리:
    #   - daemon (이거)        : body_status='pending' 만 → 신규 ingest 자동 wiki body
    #   - batch CLI (사용자)   : empty + stale 다 → 옛 23k backfill
    # 두 set 가 disjoint (daemon 은 옛 empty 안 건드림) — 동시 실행 안전.
    # default ON, env LINKMIND_WIKI_WRITER_DAEMON=0 으로 명시 disable.
    import os
    daemon_enabled = os.getenv("LINKMIND_WIKI_WRITER_DAEMON", "1") != "0"
    wiki_writer_stop: asyncio.Event | None = None
    wiki_writer_task = None
    if daemon_enabled:
        logger.info(
            "wiki_writer_worker daemon ENABLED (default) — stale 자동 처리. "
            "끄려면 env LINKMIND_WIKI_WRITER_DAEMON=0"
        )
        wiki_writer_stop = asyncio.Event()
        wiki_writer_task = asyncio.create_task(
            run_wiki_writer_worker(stop_event=wiki_writer_stop),
            name="wiki_writer_worker",
        )
    else:
        logger.info(
            "wiki_writer_worker daemon DISABLED (env LINKMIND_WIKI_WRITER_DAEMON=0)"
        )

    try:
        yield
    finally:
        logger.info("LinkMind 종료 — worker 들 정리 + DB 엔진 close")
        worker_stop.set()
        if wiki_writer_stop is not None:
            wiki_writer_stop.set()
        # 각 worker 가 LLM 호출 중일 수 있어 60초 graceful 대기. 그 후 force cancel.
        tasks_to_wait = [("analysis_worker", worker_task)]
        if wiki_writer_task is not None:
            tasks_to_wait.append(("wiki_writer_worker", wiki_writer_task))
        for task_name, task in tasks_to_wait:
            try:
                await asyncio.wait_for(task, timeout=60.0)
            except asyncio.TimeoutError:
                logger.warning("%s timeout (60s) — force cancel", task_name)
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            except Exception as e:  # noqa: BLE001
                logger.warning("%s 종료 중 예외: %s", task_name, e)
        await close_engine()


app = FastAPI(
    title="LinkMind API",
    description="개인 AI Research OS — 데이터 수집/분석/검색 + 학습 데이터 export",
    version="0.1.0",
    lifespan=lifespan,
)

# 인증 미들웨어 (단계 A) — 쿠키 JWT 를 디코드해 request.state.auth 에 채움.
# CORS 는 cross-origin(frontend :3001 → backend :8000) 쿠키 전송을 위해 credentials 허용.
# 운영(SaaS)에선 allow_origins 를 실제 도메인으로 제한 + allow_credentials=True 유지.
# 주의: credentials 쿠키는 allow_origins=["*"] 와 함께 못 씀(브라우저 정책) → frontend
# origin 을 명시. config(settings) 경유 — env/dev.env 의 LINKMIND_CORS_ORIGINS 가 제대로 반영됨.
_frontend_origins = [
    o.strip() for o in settings.linkmind_cors_origins.split(",") if o.strip()
]
app.add_middleware(AuthMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_frontend_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ──────────────────────────────────────────────────────
# 보호 정책 (단계 A): 데이터/기능 라우터는 로그인 필수. 예외:
#   - health  : 인프라 모니터링 (인증 없이 접근 가능해야)
#   - auth    : 로그인 자체
#   - files   : cross-origin <img>/<iframe> inline 표시 (쿠키 자동첨부 안 되는 경우 대비).
#               단계 C 에서 file_hash → space 소속 확인으로 격리.
#   - /        : 루트 안내
_protected = [Depends(get_current_user)]

app.include_router(health.router, tags=["health"])
app.include_router(auth_api.router, prefix="/auth", tags=["auth"])
app.include_router(files.router, prefix="/files", tags=["files"])

app.include_router(ingest.router, prefix="/ingest", tags=["ingest"], dependencies=_protected)
app.include_router(search.router, prefix="/search", tags=["search"], dependencies=_protected)
app.include_router(ask.router, prefix="/ask", tags=["ask"], dependencies=_protected)
app.include_router(settings_api.router, prefix="/settings", tags=["settings"], dependencies=_protected)
app.include_router(topics.router, prefix="/topics", tags=["topics"], dependencies=_protected)
app.include_router(items.router, prefix="/items", tags=["items"], dependencies=_protected)
app.include_router(graph.router, prefix="/graph", tags=["graph"], dependencies=_protected)
app.include_router(wiki.router, prefix="/wiki", tags=["wiki"], dependencies=_protected)
app.include_router(sessions_api.router, prefix="/sessions", tags=["sessions"], dependencies=_protected)
app.include_router(admin_arxiv.router, prefix="/admin/arxiv", tags=["admin"], dependencies=_protected)


@app.get("/")
async def root() -> dict:
    return {
        "name": "LinkMind",
        "version": app.version,
        "docs": "/docs",
        "purpose": "raw-first knowledge OS for personal sVLL training",
    }
