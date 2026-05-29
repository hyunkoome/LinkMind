"""
키워드 정규화 일괄 적용 (2026-05-29) — 기존 wiki_pages.keywords 재작성.

사용자 규칙 (backend/utils/keywords.normalize_keywords):
  - 영문 only — 중국어(CJK)/일본어/한글 포함 키워드 삭제.
  - 소문자-대시 slug — CloudCompare/Cloud Compare → cloud-compare, TreeAIBox → tree-ai-box.
  - 정규화 후 중복 제거.

idempotent — 이미 정규화된 키워드는 그대로라 재실행해도 안전 (변경 0).

사용:
  python -m backend.jobs.normalize_keywords --dry-run   # 통계 + 샘플만 (변경 X)
  python -m backend.jobs.normalize_keywords             # 실제 적용
  python -m backend.jobs.normalize_keywords --limit 50  # 디버깅 (50 wiki 만)

⚠ wiki backfill daemon(pending 합성) 이 도는 동안은 사용자 요청으로 보류 — pending=0
  후 실행. 동시 실행해도 치명적이진 않으나(다른 컬럼) 깔끔하게 순차.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

from sqlalchemy import text
from tqdm import tqdm

from backend.db.connection import close_engine, get_session_factory
from backend.utils.keywords import normalize_keywords

_FETCH_SQL = text("""
    SELECT id, keywords FROM wiki_pages
    WHERE keywords IS NOT NULL AND array_length(keywords, 1) > 0
""")

_UPDATE_SQL = text("""
    UPDATE wiki_pages SET keywords = CAST(:kw AS text[]) WHERE id = :id
""")

_COMMIT_EVERY = 500


async def main(dry_run: bool, limit: int | None) -> None:
    Session = get_session_factory()
    print("=" * 72, flush=True)
    print(f"normalize_keywords — dry_run={dry_run} limit={limit or '전체'}", flush=True)
    print("=" * 72, flush=True)

    async with Session() as s:
        rows = [
            (str(r["id"]), list(r["keywords"] or []))
            for r in (await s.execute(_FETCH_SQL)).mappings().all()
        ]
    if limit:
        rows = rows[:limit]

    plan: list[tuple[str, list[str]]] = []   # (wiki_id, new_keywords) — 변경분만
    total_before = total_after = 0
    samples: list[tuple[list[str], list[str]]] = []
    for wid, kws in rows:
        new = normalize_keywords(kws)
        total_before += len(kws)
        total_after += len(new)
        if new != kws:
            plan.append((wid, new))
            if len(samples) < 14:
                samples.append((kws, new))

    print(f"\nwiki(키워드 보유): {len(rows)}", flush=True)
    print(f"변경될 wiki: {len(plan)}", flush=True)
    print(f"키워드 총합: {total_before} → {total_after} "
          f"(삭제/병합 {total_before - total_after})", flush=True)
    if samples:
        print("\n  [변경 샘플]", flush=True)
        for before, after in samples:
            print(f"    {before}\n      → {after}", flush=True)

    if dry_run:
        print("\n✅ DRY RUN — 변경 없음. 실제: --dry-run 빼고 재호출", flush=True)
        return

    start = time.monotonic()
    done = 0
    async with Session() as session:
        for wid, new in tqdm(plan, desc="🔤 키워드 정규화", unit="wiki", mininterval=0.5):
            await session.execute(_UPDATE_SQL, {"kw": new, "id": wid})
            done += 1
            if done % _COMMIT_EVERY == 0:
                await session.commit()
        await session.commit()

    print("\n" + "=" * 72, flush=True)
    print(f"✅ 완료 — {done} wiki 키워드 정규화 ({time.monotonic() - start:.0f}s)", flush=True)


async def _entry(dry_run: bool, limit: int | None) -> None:
    try:
        await main(dry_run, limit)
    finally:
        await close_engine()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="wiki_pages.keywords 정규화 (영문 slug)")
    parser.add_argument("--dry-run", action="store_true", help="통계+샘플만, 변경 X")
    parser.add_argument("--limit", type=int, default=None, help="N wiki 만 (디버깅)")
    args = parser.parse_args()
    asyncio.run(_entry(args.dry_run, args.limit))
    sys.stdout.flush()
