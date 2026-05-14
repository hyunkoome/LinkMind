"""
scripts/backfill_summary.py
----------------------------------------------------------------------------
items.summary IS NULL 인 row 의 raw_content 를 LLM 으로 요약해서 채움.

이미 ingest 된 자료에 새 요약 로직을 소급 적용할 때 사용. 예:
  - ingest_url 에 요약 단계가 나중에 추가되어 기존 row 가 summary 없음
  - prompt 버전을 올린 뒤 전체 재요약 (--force 로)

사용:
    python scripts/backfill_summary.py                # summary IS NULL 인 모든 item
    python scripts/backfill_summary.py <item_id>      # 특정 item 1개
    python scripts/backfill_summary.py --force        # summary 있어도 모두 재생성
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from uuid import UUID

# 프로젝트 루트를 sys.path 에 추가 (스크립트로 실행 시 필요).
# scripts/step4_init_qdrant.py 와 동일한 패턴.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker  # noqa: E402

from backend.db.connection import get_engine  # noqa: E402
from backend.ingest.url import _generate_and_save_summary  # noqa: E402


async def _fetch_targets(session: AsyncSession, *, item_id: UUID | None, force: bool) -> list[tuple[UUID, str]]:
    if item_id is not None:
        rows = await session.execute(
            text("SELECT id, raw_content FROM items WHERE id = :id"),
            {"id": str(item_id)},
        )
    elif force:
        rows = await session.execute(text("SELECT id, raw_content FROM items ORDER BY ingested_at"))
    else:
        rows = await session.execute(
            text("SELECT id, raw_content FROM items WHERE summary IS NULL ORDER BY ingested_at")
        )
    return [(r.id, r.raw_content) for r in rows.all()]


async def main() -> int:
    args = sys.argv[1:]
    force = "--force" in args
    args = [a for a in args if a != "--force"]
    item_id: UUID | None = UUID(args[0]) if args else None

    engine = get_engine()
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with session_factory() as session:
        targets = await _fetch_targets(session, item_id=item_id, force=force)
        if not targets:
            print("대상 없음 (이미 모두 summary 보유, 또는 item 미존재).")
            return 0

        print(f"대상 {len(targets)} 건 — 요약 생성 시작")
        ok, fail = 0, 0
        for iid, raw in targets:
            print(f"  - {iid} ...", end=" ", flush=True)
            res = await _generate_and_save_summary(session, item_id=iid, text=raw)
            if res:
                print(f"OK ({len(res)} chars)")
                ok += 1
            else:
                print("실패")
                fail += 1
        print(f"\n완료: 성공 {ok} / 실패 {fail}")
        return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
