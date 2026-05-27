"""
D10 wave-2a (2026-05-26) — wiki body 일괄 backfill.

배경 (사용자 요구):
  - wave-1g backfill: topics 23,940 → wiki_pages 23,852 (body 없이 link 만)
  - 사용자가 페이지 클릭할 때마다 body 합성은 비효율 (23k 페이지를 일일이 클릭 X)
  - 백그라운드 일괄 처리 + 진행률 + ETA 표시 필요

설계:
  - body_status='issues' 또는 'pending' 인 wiki_pages 다 처리
  - tqdm 진행률 + 페이지당 소요 + ETA + 누적 통계
  - vLLM GPU 한 모델 sequential — concurrency=1 자연
  - 실패한 page 도 skip + log (다음 실행 때 재시도)
  - SIGINT / SIGTERM 안전 (graceful — 현재 page 끝나면 종료)
  - 옵션:
      --limit N      : 일부만 처리 (테스트)
      --status x,y   : 특정 status 만 (default: ready,pending)
      --slug PATTERN : slug 매칭만 (예: 'arxiv*' 또는 'github*')
      --resume       : 마지막 실행에서 처리 안 된 것부터 (default 동작)
      --dry-run      : 실제 합성 안 함, 처리 대상만 표시

사용:
  python -m backend.jobs.wiki_writer_batch                  # 모든 empty/stale 처리
  python -m backend.jobs.wiki_writer_batch --limit 100      # 100 페이지만 (테스트)
  python -m backend.jobs.wiki_writer_batch --status empty   # empty 만
  python -m backend.jobs.wiki_writer_batch --slug 'arxiv*'  # arxiv* 만
  python -m backend.jobs.wiki_writer_batch --dry-run        # 처리 대상 확인

  # 백그라운드 실행 + log 파일
  nohup python -m backend.jobs.wiki_writer_batch > /tmp/wiki_backfill.log 2>&1 &
  tail -f /tmp/wiki_backfill.log
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import text
from tqdm import tqdm

from backend.agents import AgentContext
from backend.agents.writer import WriterAgent
from backend.db.connection import close_engine, get_session_factory


# ─────────────────────────────────────────────────────────────────────────────
# PID lock — 사용자 실수로 batch 두 번 실행 방지 (vLLM sequential 이라 동시 의미 X)
# ─────────────────────────────────────────────────────────────────────────────

_PID_FILE = Path("/tmp/linkmind-wiki-writer-batch.pid")


def _acquire_pid_lock() -> None:
    """이미 batch 가 돌고 있으면 즉시 종료. 끝나면 자동 release (atexit).

    shell wrapper (scripts/run_wiki_backfill.sh) 가 호출 시: env
    LINKMIND_WIKI_BATCH_LOCK_OWNER=shell 설정 → 이 함수가 lock 검사 skip
    (shell 이 PID file 관리). 사용자가 python 직접 호출 시만 lock 적용.
    """
    if os.getenv("LINKMIND_WIKI_BATCH_LOCK_OWNER") == "shell":
        # shell wrapper 가 PID file 책임 — python 은 검사/등록 안 함
        return
    if _PID_FILE.exists():
        try:
            old_pid = int(_PID_FILE.read_text().strip())
        except (ValueError, OSError):
            old_pid = -1
        # 진짜 살아있는 프로세스인지 확인 (PID 재사용 회피)
        if old_pid > 0:
            try:
                os.kill(old_pid, 0)   # signal 0 = 존재 검사만
                print(
                    f"❌  이미 wiki_writer_batch 가 실행 중 (PID={old_pid}).\n"
                    f"   중복 실행 안전 X — vLLM sequential queue 경쟁만 발생.\n"
                    f"   종료 명령: kill -INT {old_pid}\n"
                    f"   강제 종료: kill -KILL {old_pid} && rm {_PID_FILE}\n"
                    f"   PID 파일: {_PID_FILE}"
                )
                sys.exit(1)
            except ProcessLookupError:
                # PID 살아있지 않음 — stale lock. 인계.
                print(f"⚠️  옛 PID 파일 발견 (PID={old_pid} 죽었음) — stale lock 제거")
                _PID_FILE.unlink(missing_ok=True)
    _PID_FILE.write_text(str(os.getpid()))
    # 종료 시 자동 cleanup
    import atexit
    atexit.register(lambda: _PID_FILE.unlink(missing_ok=True))

logging.basicConfig(
    level=logging.WARNING,    # tqdm 출력 보호 (INFO 는 너무 시끄러움)
    format="%(asctime)s %(levelname)s %(name)s : %(message)s",
)
logger = logging.getLogger("linkmind.jobs.wiki_writer_batch")


# ─────────────────────────────────────────────────────────────────────────────
# SQL
# ─────────────────────────────────────────────────────────────────────────────

# tqdm desc/postfix 용 통계 query — fast
_COUNT_PENDING_SQL = text("""
    SELECT
      COUNT(*) FILTER (WHERE body_status = 'issues')      AS empty_count,
      COUNT(*) FILTER (WHERE body_status = 'pending')      AS stale_count,
      COUNT(*) FILTER (WHERE body_status = 'completed')      AS issues_count,
      COUNT(*) FILTER (WHERE body_status = 'pending') AS gen_count,
      COUNT(*)                                            AS total_count
    FROM wiki_pages
""")

# 처리 대상 fetch — body_status + slug 필터. concurrent N 마다 batch fetch.
# row 1 개 fetch + UPDATE 한 후 다음 fetch — concurrent worker 들이 같은 row 안 잡도록
# 즉시 'pending' 마킹 (writer 가 다시 'completed' 로 변경).
_FETCH_NEXT_PAGE_SQL = text("""
    UPDATE wiki_pages wp
    SET body_status = 'pending', body_processing_started_at = now()
    WHERE id = (
        SELECT id FROM wiki_pages
        WHERE body_status = ANY(:statuses)
          AND (CAST(:slug_pattern AS TEXT) IS NULL OR slug LIKE CAST(:slug_pattern AS TEXT))
          AND EXISTS (
              SELECT 1 FROM wiki_page_items wpi
              WHERE wpi.wiki_page_id = wiki_pages.id
                AND (wpi.user_action IS NULL OR wpi.user_action != 'removed')
          )
        ORDER BY is_pinned DESC, updated_at ASC
        LIMIT 1
        FOR UPDATE SKIP LOCKED         -- concurrent worker race 방지
    )
    RETURNING id, slug, title,
       (SELECT COUNT(*) FROM wiki_page_items wpi
            WHERE wpi.wiki_page_id = wp.id
              AND (wpi.user_action IS NULL OR wpi.user_action != 'removed')
       ) AS source_count
""")


# ─────────────────────────────────────────────────────────────────────────────
# signal — SIGINT 시 graceful (현재 page 끝나고 종료)
# ─────────────────────────────────────────────────────────────────────────────

_stop_requested = False


def _install_signal_handlers() -> None:
    def handler(signum, _frame):
        global _stop_requested
        if _stop_requested:
            # 두 번째 Ctrl+C → 즉시 종료
            print("\n⚠️  강제 종료 (현재 page 미완)")
            sys.exit(130)
        _stop_requested = True
        print("\n⚠️  종료 신호 받음 — 현재 page 끝나면 종료. 다시 Ctrl+C 면 즉시.")
    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)


# ─────────────────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────────────────

async def _worker_loop(
    worker_id: int,
    session_factory,
    statuses: list[str],
    slug_pattern: str | None,
    stats: dict,
    bar: "tqdm",
    target: int,
) -> None:
    """concurrent worker — 다음 page fetch (SKIP LOCKED) + 처리 loop.

    여러 worker 동시 실행 시 vLLM 의 continuous batching 활용 (~3x throughput).
    각 worker = 자체 session + 자체 WriterAgent 인스턴스 (state-centric, race-free).
    """
    while True:
        if _stop_requested:
            return
        if stats["processed"] >= target:
            return

        # 다음 page fetch — SKIP LOCKED 로 다른 worker 가 잡은 row skip
        async with session_factory() as session:
            async with session.begin():
                row = (await session.execute(
                    _FETCH_NEXT_PAGE_SQL,
                    {"statuses": statuses, "slug_pattern": slug_pattern},
                )).mappings().first()

        if not row:
            # 다른 worker 가 마지막 row 가져갔거나 모두 처리 — 종료
            return

        page_id = row["id"]
        slug = row["slug"]
        source_count = int(row["source_count"] or 0)

        if source_count == 0:
            # source 0 page — 자동 empty 로 reset (generating 으로 임시 마킹된 상태)
            async with session_factory() as session:
                async with session.begin():
                    await session.execute(text(
                        "UPDATE wiki_pages SET body_status = 'issues', body_processing_started_at = NULL WHERE id = :pid"
                    ), {"pid": str(page_id)})
            stats["skip"] += 1
            stats["processed"] += 1
            bar.update(1)
            bar.set_postfix_str(
                f"ok={stats['ok']} fail={stats['fail']} skip={stats['skip']} (w{worker_id} 0 sources)",
                refresh=False,
            )
            continue

        # 실제 합성
        page_start = time.monotonic()
        body_len = 0
        err_msg: str | None = None
        try:
            async with session_factory() as session:
                ctx = AgentContext(
                    session=session,
                    related_wiki_page_id=UUID(str(page_id)) if not isinstance(page_id, UUID) else page_id,
                    extra={"trigger_reason": "batch_backfill"},
                )
                writer = WriterAgent()        # task 별 새 인스턴스 (self._ctx race-free)
                result = await writer.run(ctx)
                await session.commit()
            if result.ok:
                body_len = (result.output_meta or {}).get("body_length", 0)
                stats["ok"] += 1
                stats["body_chars"] += body_len
            else:
                err_msg = result.error
                stats["fail"] += 1
                # 실패 → empty 로 reset (다음 batch 가 재시도)
                async with session_factory() as session:
                    async with session.begin():
                        await session.execute(text(
                            "UPDATE wiki_pages SET body_status = 'issues', body_processing_started_at = NULL WHERE id = :pid"
                        ), {"pid": str(page_id)})
        except Exception as exc:  # noqa: BLE001
            err_msg = f"{type(exc).__name__}: {exc}"
            stats["fail"] += 1
            async with session_factory() as session:
                async with session.begin():
                    await session.execute(text(
                        "UPDATE wiki_pages SET body_status = 'issues', body_processing_started_at = NULL WHERE id = :pid"
                    ), {"pid": str(page_id)})

        page_dur = time.monotonic() - page_start
        stats["processed"] += 1
        avg_chars = stats["body_chars"] / max(stats["ok"], 1)

        bar.update(1)
        bar.set_postfix_str(
            f"ok={stats['ok']} fail={stats['fail']} skip={stats['skip']} "
            f"avg={avg_chars:.0f}자 last={body_len}자 {page_dur:.1f}s "
            f"w{worker_id}={slug[:25]}",
            refresh=False,
        )
        if err_msg:
            bar.write(f"  ⚠️  w{worker_id} {slug} : {err_msg[:120]}")


async def main(
    statuses: list[str],
    slug_pattern: str | None,
    limit: int,
    dry_run: bool,
    concurrency: int = 4,
) -> None:
    # 중복 실행 방지 (vLLM sequential 이라 동시 의미 X) — dry-run 은 skip
    if not dry_run:
        _acquire_pid_lock()

    session_factory = get_session_factory()

    # 사전 통계
    async with session_factory() as session:
        async with session.begin():
            stats = (await session.execute(_COUNT_PENDING_SQL)).mappings().first()
            params: dict[str, Any] = {"statuses": statuses, "slug_pattern": slug_pattern}
            sample_query = text("""
                SELECT COUNT(*) FROM wiki_pages
                WHERE body_status = ANY(:statuses)
                  AND (CAST(:slug_pattern AS TEXT) IS NULL OR slug LIKE CAST(:slug_pattern AS TEXT))
            """)
            pending = (await session.execute(sample_query, params)).scalar() or 0

    target = min(pending, limit)
    print(f"\n📊 wiki_pages 현재 — total={stats['total_count']}, "
          f"empty={stats['empty_count']}, stale={stats['stale_count']}, "
          f"ready={stats['issues_count']}, generating={stats['gen_count']}")
    print(f"📝 처리 대상 — status IN ({','.join(statuses)})"
          f"{f' AND slug LIKE {slug_pattern!r}' if slug_pattern else ''}"
          f" → {pending} pages 중 limit={limit} → 실제 {target} 처리\n")

    if dry_run:
        print("--- DRY RUN: 처리 안 함, 처리 대상 sample 만 ---")
        async with session_factory() as session:
            async with session.begin():
                sample = (await session.execute(text("""
                    SELECT slug, title, body_status,
                       (SELECT COUNT(*) FROM wiki_page_items wpi
                            WHERE wpi.wiki_page_id = wp.id
                              AND (wpi.user_action IS NULL OR wpi.user_action != 'removed')
                       ) AS source_count
                    FROM wiki_pages wp
                    WHERE body_status = ANY(:statuses)
                      AND (CAST(:slug_pattern AS TEXT) IS NULL OR slug LIKE CAST(:slug_pattern AS TEXT))
                    ORDER BY is_pinned DESC, updated_at ASC
                    LIMIT 10
                """), {"statuses": statuses, "slug_pattern": slug_pattern})).mappings().all()
        for r in sample:
            print(f"  • [{r['body_status']}] {r['slug']} — {r['title'][:60]} (sources={r['source_count']})")
        await close_engine()
        return

    if target == 0:
        print("✅ 처리할 wiki_page 없음 — 종료")
        await close_engine()
        return

    # ─────────── tqdm 진행률 + ETA + 통계 ───────────
    bar = tqdm(
        total=target,
        desc=f"🪄 wiki body 합성 (concurrency={concurrency})",
        unit="page",
        smoothing=0.1,         # 최근 페이지 위주 ETA (vLLM 변동 안정)
        mininterval=0.5,
        bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}{postfix}]",
    )

    stats = {"ok": 0, "fail": 0, "skip": 0, "processed": 0, "body_chars": 0}
    start_ts = time.monotonic()

    _install_signal_handlers()

    try:
        # concurrent N worker — 각자 다음 page fetch (SKIP LOCKED) + 처리.
        # vLLM continuous batching 이 자동 (동시 N HTTP 호출 → 같은 forward pass batch)
        workers = [
            asyncio.create_task(
                _worker_loop(i + 1, session_factory, statuses, slug_pattern, stats, bar, target),
                name=f"wiki_writer_worker_{i+1}",
            )
            for i in range(concurrency)
        ]
        await asyncio.gather(*workers, return_exceptions=True)
    finally:
        bar.close()
        elapsed = time.monotonic() - start_ts
        ok = stats["ok"]
        fail = stats["fail"]
        skip = stats["skip"]
        total_done = ok + fail + skip
        print(f"\n✅ 완료 — ok={ok} fail={fail} skip={skip} "
              f"in {elapsed:.0f}s ({elapsed/60:.1f}분, {elapsed/3600:.2f}시간)")
        if ok > 0:
            print(f"   평균 body 길이: {stats['body_chars'] / ok:.0f}자, "
                  f"페이지당 평균 {elapsed / max(total_done, 1):.1f}s "
                  f"(concurrency={concurrency} 자연 throughput)")
        await close_engine()
        return

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", default="ready,pending",
                    help="처리할 body_status (콤마 구분, default: ready,pending)")
    ap.add_argument("--slug", default=None,
                    help="slug LIKE 패턴 (예: 'arxiv*' 또는 'github__%')")
    ap.add_argument("--limit", type=int, default=1000000,
                    help="처리할 최대 갯수 (default: unbounded)")
    ap.add_argument("--dry-run", action="store_true", help="처리 안 함")
    ap.add_argument("--concurrency", type=int, default=4,
                    help="동시 worker 갯수 (default 4, vLLM continuous batching ~3x throughput)")
    args = ap.parse_args()

    statuses = [s.strip() for s in args.status.split(",") if s.strip()]
    asyncio.run(main(
        statuses=statuses,
        slug_pattern=args.slug,
        limit=args.limit,
        dry_run=args.dry_run,
        concurrency=args.concurrency,
    ))
