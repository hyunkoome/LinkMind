"""
arxiv OAI-PMH 증분 수확 job — 최근 갱신된 arXiv 메타를 arxiv_papers 에 적재.

watermark(app_settings['arxiv_oai_last_from'])부터 오늘까지 수확 → upsert → watermark
갱신. 전체 backfill 은 Kaggle 이 담당하고, 이 job 은 **증분 전용**(매일 from=어제).

사용:
  python -m backend.jobs.arxiv_harvest_oai                 # watermark(없으면 7일 전)부터
  python -m backend.jobs.arxiv_harvest_oai --from 2024-01-01
  python -m backend.jobs.arxiv_harvest_oai --from 2025-01-01 --sets cs,eess,stat  # 분야 한정(빠름)
  python -m backend.jobs.arxiv_harvest_oai --from 2025-01-01 --max-pages 3   # 소량 테스트
  python -m backend.jobs.arxiv_harvest_oai --no-watermark   # watermark 갱신 안 함(테스트)

분야(set) 미지정이면 전체 arxiv(cs/eess/stat/math/physics 등 모두). 사용자 도메인 위주로
빠르게 받으려면 --sets cs,eess,stat (ML/영상/신호/통계/압축 관련 분야).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from arxiv_harvester.oai import harvest_oai
from backend.db import repository
from backend.db.connection import get_session_factory

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("arxiv_harvest_oai")

_WATERMARK_KEY = "arxiv_oai_last_from"


async def run(
    *,
    from_date: str | None = None,
    sets: list[str] | None = None,
    max_pages: int | None = None,
    delay: float = 3.0,
    update_watermark: bool = True,
) -> int:
    factory = get_session_factory()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # 시작 날짜: 인자 > watermark > 7일 전(첫 실행 안전 default)
    if from_date is None:
        async with factory() as s:
            wm = await repository.get_app_setting(s, _WATERMARK_KEY)
        from_date = wm or (
            datetime.now(timezone.utc) - timedelta(days=7)
        ).strftime("%Y-%m-%d")

    # 분야(set) 한정 — 미지정이면 [None] = 전체 arxiv. 여러 set 은 순차 수확.
    set_list: list[str | None] = sets if sets else [None]
    logger.info("OAI 증분 수확 시작 — from=%s sets=%s (today=%s)",
                from_date, sets or "전체", today)
    total = 0
    for set_spec in set_list:
        page = 0
        async for batch in harvest_oai(
            from_date, set_spec=set_spec, delay=delay, max_pages=max_pages,
        ):
            page += 1
            if not batch:
                continue
            async with factory() as s:
                n = await repository.upsert_arxiv_papers(s, batch)
                await s.commit()
            total += n
            logger.info("  [%s] page %d: +%d (누적 %d)", set_spec or "전체", page, n, total)

    if update_watermark:
        async with factory() as s:
            await repository.set_app_setting(s, _WATERMARK_KEY, today)
            await s.commit()
        logger.info("watermark 갱신 → %s", today)

    logger.info("✅ OAI 수확 완료 — %d papers upsert", total)
    return total


def main() -> None:
    ap = argparse.ArgumentParser(description="arXiv OAI-PMH 증분 수확")
    ap.add_argument("--from", dest="from_date", default=None, help="YYYY-MM-DD (없으면 watermark/7일전)")
    ap.add_argument("--sets", default=None, help="분야 한정 콤마구분 (예: cs,eess). 미지정=전체 arxiv")
    ap.add_argument("--max-pages", type=int, default=None, help="페이지 상한 (테스트/소량)")
    ap.add_argument("--delay", type=float, default=3.0, help="페이지 간 대기 초")
    ap.add_argument("--no-watermark", action="store_true", help="watermark 갱신 안 함")
    args = ap.parse_args()
    sets = [s.strip() for s in args.sets.split(",") if s.strip()] if args.sets else None
    asyncio.run(run(
        from_date=args.from_date, sets=sets, max_pages=args.max_pages,
        delay=args.delay, update_watermark=not args.no_watermark,
    ))


if __name__ == "__main__":
    main()
