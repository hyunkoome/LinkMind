"""
backend/jobs/regenerate_foreign_wikis.py
─────────────────────────────────────────
이미 저장된 wiki_pages.body 중 **중국어/일본어가 섞인** 페이지를 찾아 일괄 재생성.

배경: 본문 합성 모델(Qwen2.5-7B)은 중국 모델이라 source 가 중국어/일본어면
프롬프트의 "중국어 금지" 규칙을 어기고 그 언어로 본문을 써버렸다. writer 에
언어 안전장치(재생성 루프)를 넣은 뒤(2026-05-30), 그 이전에 생성된 오염 위키를
이 job 으로 정리한다. 재생성하면 writer 의 새 루프가 한국어로 다시 쓴다.

처리 흐름:
  1) body_status='completed' 이면서 body 에 한자/가나가 1자라도 있는 후보를 SQL 로
     1차 SELECT (FOREIGN_CJK_SQL_PATTERN).
  2) Python count_foreign_cjk 로 임계(FOREIGN_THRESHOLD) 초과만 정밀 선별
     — 한자 1~2 자(고유명사) 섞인 정상 한국어 위키는 제외.
  3) asyncio.Queue + concurrency worker 로 WriterAgent.run() 재호출.

idempotent — 재실행 안전. writer 가 한국어로 다시 쓰면 다음 실행 때 후보에서 빠진다.

⚠️ GPU 공유 — vLLM 이 별 컨테이너면 backend 가 떠 있어도 되지만, ingest/backfill 이
   동시에 돌면 vLLM 부하가 겹친다. 텔레그램 ingest 가 끝난 뒤 실행 권장.

실행:
  python -m backend.jobs.regenerate_foreign_wikis --dry-run        # 대상 수 + sample 만
  python -m backend.jobs.regenerate_foreign_wikis                  # 전체 재생성 (concurrency 4)
  python -m backend.jobs.regenerate_foreign_wikis --limit 50       # 50 page 만
  python -m backend.jobs.regenerate_foreign_wikis --concurrency 2  # vLLM 부하 낮게
  python -m backend.jobs.regenerate_foreign_wikis --slug 'github__%'  # slug 패턴만
  bash scripts/run_foreign_wiki_backfill.sh [--dry-run|--limit N|...]  # shell 래퍼
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
import time
from typing import Any
from uuid import UUID

from sqlalchemy import text
from tqdm import tqdm

from backend.agents import AgentContext
from backend.agents.writer import WriterAgent
from backend.db.connection import close_engine, get_session_factory
from backend.utils.lang import (
    FOREIGN_CJK_SQL_PATTERN,
    FOREIGN_THRESHOLD,
    count_foreign_cjk,
)

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s : %(message)s",
)
logger = logging.getLogger("linkmind.jobs.regenerate_foreign_wikis")


# 1차 후보 SELECT — completed + body 에 한자/가나 1자라도 + 살아있는 source 존재.
# (~ 는 Postgres POSIX regex. user_action='removed' 만 남은 wiki 는 재생성 의미 X.)
_FETCH_CANDIDATES_SQL = text("""
    SELECT id, slug, title, body
    FROM wiki_pages
    WHERE body_status = 'completed'
      AND body IS NOT NULL
      AND body ~ :foreign_pat
      AND (CAST(:slug_pattern AS TEXT) IS NULL OR slug LIKE CAST(:slug_pattern AS TEXT))
      AND EXISTS (
          SELECT 1 FROM wiki_page_items wpi
          WHERE wpi.wiki_page_id = wiki_pages.id
            AND (wpi.user_action IS NULL OR wpi.user_action != 'removed')
      )
    ORDER BY updated_at ASC
""")


_stop_requested = False


def _install_signal_handlers() -> None:
    def handler(signum, _frame):  # noqa: ANN001, ARG001
        global _stop_requested
        if _stop_requested:
            print("\n⚠️  강제 종료 (현재 page 미완)")
            sys.exit(130)
        _stop_requested = True
        print("\n⚠️  종료 신호 — 현재 처리 중인 page 끝나면 종료. 다시 Ctrl+C 면 즉시.")

    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)


async def _worker_loop(
    worker_id: int,
    session_factory,  # noqa: ANN001
    queue: "asyncio.Queue[tuple[Any, str, int]]",
    stats: dict[str, int],
    bar: "tqdm",
) -> None:
    """queue 에서 (page_id, slug, before_count) 를 꺼내 WriterAgent 재호출."""
    while not _stop_requested:
        try:
            page_id, slug, before_count = queue.get_nowait()
        except asyncio.QueueEmpty:
            return

        page_start = time.monotonic()
        err_msg: str | None = None
        try:
            async with session_factory() as session:
                ctx = AgentContext(
                    session=session,
                    related_wiki_page_id=(
                        page_id if isinstance(page_id, UUID) else UUID(str(page_id))
                    ),
                    extra={"trigger_reason": "foreign_language_regenerate"},
                )
                writer = WriterAgent()  # task 별 새 인스턴스 (self._ctx race-free)
                result = await writer.run(ctx)
                await session.commit()

            if result.ok:
                stats["ok"] += 1
            else:
                err_msg = result.error
                stats["fail"] += 1
        except Exception as exc:  # noqa: BLE001
            err_msg = f"{type(exc).__name__}: {exc}"
            stats["fail"] += 1

        page_dur = time.monotonic() - page_start
        stats["processed"] += 1
        bar.update(1)
        bar.set_postfix_str(
            f"ok={stats['ok']} fail={stats['fail']} {page_dur:.1f}s "
            f"w{worker_id}={slug[:24]}",
            refresh=False,
        )
        if err_msg:
            bar.write(f"  ⚠️  w{worker_id} {slug} (외국어 {before_count}자) : {err_msg[:120]}")


async def main(
    *,
    slug_pattern: str | None,
    limit: int,
    dry_run: bool,
    concurrency: int,
) -> None:
    session_factory = get_session_factory()

    # 1) 후보 SELECT + Python 정밀 필터 (임계 초과만)
    async with session_factory() as session:
        rows = (
            await session.execute(
                _FETCH_CANDIDATES_SQL,
                {"foreign_pat": FOREIGN_CJK_SQL_PATTERN, "slug_pattern": slug_pattern},
            )
        ).mappings().all()

    targets: list[tuple[Any, str, int]] = []
    for r in rows:
        cnt = count_foreign_cjk(r["body"])
        # SQL 후보(한자/가나 1자+) 중 임계 초과만 (has_foreign_script 와 동일 기준이되
        # 카운트를 sample 표시용으로 같이 쓰려고 직접 비교).
        if cnt > FOREIGN_THRESHOLD and r["id"] is not None:
            targets.append((r["id"], r["slug"], cnt))

    total_candidates = len(targets)
    print(
        f"\n📝 중국어/일본어 섞인 wiki body — SQL 후보 {len(rows)} 중 "
        f"임계 초과 {total_candidates} pages"
    )

    if total_candidates == 0:
        print("✅ 재생성할 외국어 오염 위키 없음")
        await close_engine()
        return

    # 외국어 글자 많은 순으로 sample 출력 (오염 심한 것 먼저 확인)
    sample = sorted(targets, key=lambda t: t[2], reverse=True)[:10]
    print("   가장 오염 심한 sample (외국어 글자 수):")
    for _pid, slug, cnt in sample:
        print(f"     - {cnt:>4}자  {slug}")

    targets = targets[:limit]
    print(f"\n→ 이번에 재생성: {len(targets)} pages (concurrency={concurrency})\n")

    if dry_run:
        print("--- DRY RUN: 실제 재생성 안 함 ---")
        await close_engine()
        return

    queue: "asyncio.Queue[tuple[Any, str, int]]" = asyncio.Queue()
    for t in targets:
        queue.put_nowait(t)

    bar = tqdm(
        total=len(targets),
        desc=f"🔄 외국어 wiki 재생성 (concurrency={concurrency})",
        unit="page",
        smoothing=0.1,
        mininterval=0.5,
        bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}{postfix}]",
    )

    stats = {"ok": 0, "fail": 0, "processed": 0}
    start_ts = time.monotonic()
    _install_signal_handlers()

    try:
        workers = [
            asyncio.create_task(
                _worker_loop(i + 1, session_factory, queue, stats, bar),
                name=f"foreign_regen_worker_{i+1}",
            )
            for i in range(concurrency)
        ]
        await asyncio.gather(*workers, return_exceptions=True)
    finally:
        bar.close()
        elapsed = time.monotonic() - start_ts
        print(
            f"\n✅ 완료 — ok={stats['ok']} fail={stats['fail']} "
            f"in {elapsed:.0f}s ({elapsed / 60:.1f}분)"
        )
        if stats["fail"]:
            print("   실패 page 는 body 가 그대로 남음 — 재실행하면 다시 후보로 잡힘.")
        await close_engine()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="중국어/일본어 섞인 wiki body 일괄 재생성",
    )
    ap.add_argument("--slug", default=None, help="slug LIKE 패턴 (예: 'github__%')")
    ap.add_argument("--limit", type=int, default=1_000_000, help="최대 갯수 (default: 전체)")
    ap.add_argument("--dry-run", action="store_true", help="대상 수 + sample 만, 재생성 X")
    ap.add_argument("--concurrency", type=int, default=4, help="동시 worker (default 4)")
    args = ap.parse_args()

    asyncio.run(
        main(
            slug_pattern=args.slug,
            limit=args.limit,
            dry_run=args.dry_run,
            concurrency=args.concurrency,
        )
    )
