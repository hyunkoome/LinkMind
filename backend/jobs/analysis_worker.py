"""
analysis_worker — background async task. summary IS NULL 인 item 을 주기적으로
찾아서 chunks (embedding) + summary 를 생성.

배경 (사용자 architecture 비판, 2026-05-18):
- 텔레그램 ingest 가 동기로 LLM summary 까지 처리하면 1메시지 ~30-60초 → 사용자가
  채널 비우는 데 막힘.
- 정답: raw 데이터만 즉시 저장 + 채널 삭제, 분석은 백그라운드 worker 가 천천히.

설계:
- FastAPI lifespan 안에서 asyncio.create_task 로 실행. backend 살아있는 동안 동작.
- 무한 loop. 매 N초마다 summary IS NULL 인 item 1개 찾아서 chunks + summary 생성.
- 처리할 item 없으면 더 길게 sleep (idle 시 CPU 안 잡음).
- 실패 (LLM down 등) 시 그 item skip + log + sleep — 다음 item 으로.

backfill_summary (별 batch job) 와 차이:
- backfill_summary: 사용자가 명시적으로 한 번 실행 (대량 처리 후 종료)
- analysis_worker: backend 함께 살아있는 daemon. 새로 들어온 raw item 도 자동 처리.

둘 다 같은 _generate_and_save_summary 호출 — 동일 동작.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.db.connection import get_engine
from backend.ingest.url import (
    ExtractedDoc,
    _embed_and_index,
    _generate_and_save_summary,
)

logger = logging.getLogger("linkmind.analysis_worker")


# 처리 간격 — 1개 처리 후 잠시 쉼 (다른 요청 우선). LLM 자체가 30-60초 걸리므로
# 작게 잡아도 충분.
_INTER_ITEM_SLEEP_S = 2.0
# 처리할 item 없을 때 — idle 시 30초마다 polling.
_IDLE_SLEEP_S = 30.0


async def _fetch_one_pending(
    session,
) -> dict[str, Any] | None:
    """summary IS NULL 인 item 1개 (가장 최근 ingest 부터). raw_content 도 같이 반환.

    조건:
    - summary IS NULL
    - raw_content 가 placeholder 가 아님 (url-only fallback / binary placeholder 는 skip)
      → 'url-only fallback' 또는 'binary file:' 로 시작하면 worker 가 처리해도
        의미 있는 summary 안 나옴. skip.
    """
    res = await session.execute(
        text("""
            SELECT id, source_type, raw_content, title, source_metadata
            FROM items
            WHERE summary IS NULL
              AND raw_content IS NOT NULL
              AND length(raw_content) >= 50
              AND raw_content NOT LIKE '[url-only fallback%'
              AND raw_content NOT LIKE '[binary file:%'
            ORDER BY ingested_at DESC
            LIMIT 1
        """),
    )
    row = res.mappings().one_or_none()
    return dict(row) if row else None


async def _has_chunks(session, item_id: UUID) -> bool:
    res = await session.execute(
        text("SELECT 1 FROM chunks WHERE item_id = :id LIMIT 1"),
        {"id": item_id},
    )
    return res.first() is not None


async def _process_one(item: dict[str, Any]) -> bool:
    """한 item 의 chunks + summary 생성. 성공이면 True, 영구 skip 이면 False."""
    item_id = item["id"]
    raw = item["raw_content"] or ""
    title = item.get("title")
    if len(raw) < 50:
        return False

    engine = get_engine()
    SessionMaker = async_sessionmaker(engine, expire_on_commit=False)
    async with SessionMaker() as session:
        async with session.begin():
            # 1. chunks 없으면 먼저 임베딩 (빠름, ~1-2초)
            if not await _has_chunks(session, item_id):
                try:
                    n = await _embed_and_index(session, item_id=item_id, text=raw)
                    logger.info("chunks 생성 — item=%s, chunks=%d", item_id, n)
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "chunks 생성 실패 (item=%s): %s",
                        item_id, e,
                    )
                    # chunks 실패해도 summary 시도 — 둘은 독립

            # 2. summary 생성 (느림, ~30-60초)
            #    ExtractedDoc 의 abstract/paper_keywords 는 backfill 시점엔 모름 —
            #    raw 전체를 body 로, title 만 사용. _generate_and_save_summary 가
            #    body 를 _SUMMARY_INPUT_LIMIT 로 cap.
            doc = ExtractedDoc(body=raw, title=title, abstract=None, paper_keywords=[])
            try:
                summary_text, _tags = await _generate_and_save_summary(
                    session, item_id=item_id, doc=doc,
                )
                if summary_text:
                    logger.info(
                        "summary 생성 — item=%s, source=%s, len=%d",
                        item_id, item.get("source_type"), len(summary_text),
                    )
                    return True
                else:
                    logger.info(
                        "summary 빈 응답 — item=%s (LLM 빈 응답, 다음 시도까지 skip)",
                        item_id,
                    )
                    return False
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "summary 생성 실패 (item=%s, %s: %s)",
                    item_id, type(e).__name__, e,
                )
                return False


async def run_analysis_worker(stop_event: asyncio.Event | None = None) -> None:
    """무한 loop — summary IS NULL 인 item 을 하나씩 처리.

    stop_event 가 set 되면 종료 (lifespan 의 cleanup 단계에서 호출).
    """
    logger.info("analysis_worker 시작 — summary 없는 item 백그라운드 처리")
    while True:
        if stop_event is not None and stop_event.is_set():
            logger.info("analysis_worker 종료 신호 받음")
            break

        engine = get_engine()
        SessionMaker = async_sessionmaker(engine, expire_on_commit=False)
        item: dict[str, Any] | None = None
        try:
            async with SessionMaker() as session:
                item = await _fetch_one_pending(session)
        except Exception as e:  # noqa: BLE001
            logger.warning("pending item fetch 실패 (DB 일시 불안?): %s", e)
            await asyncio.sleep(_IDLE_SLEEP_S)
            continue

        if item is None:
            # 처리할 item 없음 — idle
            try:
                await asyncio.wait_for(
                    stop_event.wait() if stop_event else asyncio.sleep(_IDLE_SLEEP_S),
                    timeout=_IDLE_SLEEP_S,
                )
            except asyncio.TimeoutError:
                pass
            continue

        # 처리 시도. 실패해도 다음 item 으로 진행 — 같은 item 영구 loop 방지 위해
        # 실패한 item 도 잠시 sleep (다음 iteration 에서 다른 item 만나길).
        success = False
        try:
            success = await _process_one(item)
        except Exception as e:  # noqa: BLE001
            logger.exception(
                "_process_one 예상 못한 예외 (item=%s): %s",
                item["id"], e,
            )

        # 짧은 sleep — 다음 item 으로
        try:
            await asyncio.wait_for(
                stop_event.wait() if stop_event else asyncio.sleep(_INTER_ITEM_SLEEP_S),
                timeout=_INTER_ITEM_SLEEP_S if success else _INTER_ITEM_SLEEP_S * 5,
            )
        except asyncio.TimeoutError:
            pass


# 단독 실행도 가능 — `python -m backend.jobs.analysis_worker` (디버깅용)
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        asyncio.run(run_analysis_worker())
    except KeyboardInterrupt:
        logger.info("analysis_worker 사용자 Ctrl+C 종료")
