"""
wiki_writer_worker — backend lifespan 안의 background async daemon.
body_status='pending' 또는 'issues' 인 wiki_pages 를 자동으로 합성.

배경 (사용자 명시 2026-05-26):
  - "텔레그램 등에서 입력되면 자동으로 wiki body 까지 합성되게. 따로 안 돌릴거야"
  - analysis_worker 가 summary 후 classifier 호출 (wave-2c) → wiki_pages stale 마킹
  - 그 후 wiki body 합성도 자동이어야 함 (사용자 batch 안 돌림)
  - 이 daemon 가 stale page 무한 loop 처리

설계:
  - analysis_worker 와 같은 패턴 — backend lifespan 안에서 asyncio task.
  - source_count > 0 인 stale/empty page 1개씩 처리 (sequential — vLLM GPU 한 모델).
  - 처리할 page 없으면 longer sleep (idle).
  - 실패한 page 는 잠시 skip (5분 후 재시도) — 같은 page 영구 loop 방지.
  - 우선순위: stale (이미 ready 였는데 새 자료 들어옴) > pinned > empty (첫 합성).
  - stop_event 받으면 graceful 종료.

vLLM 부하 분산:
  - analysis_worker (summary) + wiki_writer_worker (body) + 사용자 클릭 + batch CLI
    = 4 곳에서 vLLM 호출. 모두 sequential 자연 queue.
  - 사용자 클릭 (eager GET /wiki/{slug}) 우선 — 이 daemon 은 짧은 sleep 안에 양보.
"""

from __future__ import annotations

import asyncio
import logging
import time
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.agents import AgentContext
from backend.agents.writer import WriterAgent
from backend.db.connection import get_engine

logger = logging.getLogger("linkmind.wiki_writer_worker")


# 처리 간격
_CONCURRENCY = 4               # 2026-05-27: daemon = batch (concurrency 4). 사용자
                               # 통일 요청 — daemon sequential vs batch concurrency
                               # 분리 의미 X. vLLM continuous batching 활용 ~4배 빠름.
_INTER_BATCH_SLEEP_S = 0.5     # 4 page batch 처리 후 짧은 sleep
_IDLE_SLEEP_S = 30.0           # 처리할 page 없을 때 polling 간격
_FAIL_BACKOFF_S = 300.0        # 실패한 page 의 재시도 backoff (5분)

# 실패한 page id 집합 — 같은 iteration 안 영구 loop 방지.
# {page_id: 다음_재시도_가능_시각(monotonic)}.
_recent_failures: dict[str, float] = {}


# 처리 대상 fetch — **stale 만** (사용자 명시 2026-05-26).
# 옛 23k empty (wave-1g backfill 의 1:1 옮김) 는 batch CLI (사용자 직접) 가 처리.
# daemon 은 신규 ingest → classifier 가 자동 마킹한 stale 만 처리.
#
# 2026-05-27: SELECT FOR UPDATE SKIP LOCKED 추가 — batch CLI 와 동시 실행 시 같은
# row 경쟁 회피 (batch 가 처리 중인 row 는 daemon 이 skip, 거꾸로도 동일).
_FETCH_NEXT_SQL = text("""
    SELECT id, slug, title
    FROM wiki_pages wp
    WHERE body_status IN ('issues', 'pending')
      -- 2026-05-27: 'issues' (신규 ingest 의 self_wiki default) 도 자동 처리.
      -- 사용자 mental: ready → pending → completed.
      AND EXISTS (
          SELECT 1 FROM wiki_page_items wpi
          WHERE wpi.wiki_page_id = wp.id
            AND (wpi.user_action IS NULL OR wpi.user_action != 'removed')
      )
    ORDER BY is_pinned DESC, updated_at ASC
    LIMIT :limit
    FOR UPDATE SKIP LOCKED
""")


async def _fetch_next_pages(session, limit: int) -> list[dict]:
    """최대 N개 처리 대상 fetch. 최근 실패한 page 는 backoff 안엔 skip."""
    rows = (await session.execute(_FETCH_NEXT_SQL, {"limit": limit})).mappings().all()
    now = time.monotonic()
    out: list[dict] = []
    for r in rows:
        pid = str(r["id"])
        retry_at = _recent_failures.get(pid)
        if retry_at and retry_at > now:
            continue
        _recent_failures.pop(pid, None)
        out.append(dict(r))
    return out


async def _process_one(page: dict) -> bool:
    """한 wiki page 의 body 합성. 성공이면 True."""
    page_id_str = str(page["id"])
    slug = page["slug"]

    engine = get_engine()
    SessionMaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with SessionMaker() as session:
            ctx = AgentContext(
                session=session,
                related_wiki_page_id=UUID(page_id_str) if not isinstance(page["id"], UUID) else page["id"],
                extra={"trigger_reason": "daemon_auto"},
            )
            writer = WriterAgent()
            result = await writer.run(ctx)
            await session.commit()
        if result.ok:
            meta = result.output_meta or {}
            logger.info(
                "wiki body 자동 합성 — slug=%s, body_len=%s, version=%s, %sms",
                slug,
                meta.get("body_length"),
                meta.get("version_number"),
                result.duration_ms,
            )
            return True
        # 실패 — error 적립 + backoff
        logger.warning(
            "wiki body 합성 실패 (slug=%s, %s) — 5분 backoff",
            slug, result.error,
        )
        _recent_failures[page_id_str] = time.monotonic() + _FAIL_BACKOFF_S
        return False
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "wiki body 합성 예외 (slug=%s, %s: %s) — 5분 backoff",
            slug, type(e).__name__, e,
        )
        _recent_failures[page_id_str] = time.monotonic() + _FAIL_BACKOFF_S
        return False


async def run_wiki_writer_worker(stop_event: asyncio.Event | None = None) -> None:
    """무한 loop — stale wiki_pages 를 자동 body 합성 (concurrency 4).

    2026-05-27: batch CLI 와 동일 로직으로 통일 — asyncio.gather concurrency 4
    (vLLM continuous batching 활용 ~4배 빠름). daemon vs batch 차이 의미 X.

    stop_event 가 set 되면 종료 (lifespan cleanup).
    """
    logger.info(
        "wiki_writer_worker 시작 — stale wiki_pages 자동 합성 daemon (concurrency=%d)",
        _CONCURRENCY,
    )
    while True:
        if stop_event is not None and stop_event.is_set():
            logger.info("wiki_writer_worker 종료 신호 받음")
            break

        engine = get_engine()
        SessionMaker = async_sessionmaker(engine, expire_on_commit=False)
        pages: list[dict] = []
        try:
            async with SessionMaker() as session:
                pages = await _fetch_next_pages(session, limit=_CONCURRENCY)
                await session.commit()      # SKIP LOCKED 의 row lock 해제
        except Exception as e:  # noqa: BLE001
            logger.warning("wiki_page fetch 실패 (DB 일시 불안?): %s", e)
            await _sleep_or_stop(stop_event, _IDLE_SLEEP_S)
            continue

        if not pages:
            # 처리할 page 없음 — idle
            await _sleep_or_stop(stop_event, _IDLE_SLEEP_S)
            continue

        # concurrency 4 동시 처리 (batch CLI 와 같은 패턴).
        results = await asyncio.gather(
            *(_process_one(p) for p in pages),
            return_exceptions=True,
        )
        ok = sum(1 for r in results if r is True)
        if ok < len(pages):
            logger.info(
                "wiki batch — %d/%d 성공 (실패는 backoff 후 재시도)",
                ok, len(pages),
            )

        # 짧은 sleep — 다른 vLLM 호출 양보
        await _sleep_or_stop(stop_event, _INTER_BATCH_SLEEP_S)


async def _sleep_or_stop(stop_event: asyncio.Event | None, sec: float) -> None:
    """sec 동안 sleep, stop_event 받으면 즉시 wake up."""
    if stop_event is None:
        await asyncio.sleep(sec)
        return
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=sec)
    except asyncio.TimeoutError:
        pass


# 단독 실행도 가능 — `python -m backend.jobs.wiki_writer_worker` (디버깅용)
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        asyncio.run(run_wiki_writer_worker())
    except KeyboardInterrupt:
        logger.info("wiki_writer_worker 사용자 Ctrl+C 종료")
