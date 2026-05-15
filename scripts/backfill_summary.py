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
from typing import Any
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker  # noqa: E402

from backend import runtime_settings  # noqa: E402
from backend.db.connection import get_engine  # noqa: E402
from backend.ingest.url import ExtractedDoc, _generate_and_save_summary  # noqa: E402


async def _fetch_targets(
    session: AsyncSession, *, item_id: UUID | None, force: bool,
) -> list[tuple[UUID, str, str | None, dict[str, Any]]]:
    """(item_id, raw_content, title, source_metadata) 튜플 목록."""
    if item_id is not None:
        rows = await session.execute(
            text("""
                SELECT id, raw_content, title, source_metadata
                FROM items WHERE id = :id
            """),
            {"id": str(item_id)},
        )
    elif force:
        rows = await session.execute(text(
            "SELECT id, raw_content, title, source_metadata FROM items "
            "ORDER BY ingested_at"
        ))
    else:
        rows = await session.execute(text(
            "SELECT id, raw_content, title, source_metadata FROM items "
            "WHERE summary IS NULL ORDER BY ingested_at"
        ))
    return [(r.id, r.raw_content, r.title, r.source_metadata or {}) for r in rows.all()]


async def main() -> int:
    args = sys.argv[1:]
    force = "--force" in args
    args = [a for a in args if a != "--force"]
    item_id: UUID | None = UUID(args[0]) if args else None

    engine = get_engine()
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    # backend 가 안 떠 있어도 별도 프로세스로 도는 backfill 이라 prompt 캐시가 비어있다.
    # seed_and_load 로 DB → 캐시 적재 — 그래야 summary_prompt_version 이 정확히 기록됨.
    await runtime_settings.seed_and_load()

    async with session_factory() as session:
        targets = await _fetch_targets(session, item_id=item_id, force=force)
        if not targets:
            print("대상 없음 (이미 모두 summary 보유, 또는 item 미존재).")
            return 0

        print(f"대상 {len(targets)} 건 — 요약 생성 시작")
        ok, fail = 0, 0
        for iid, raw, title, meta in targets:
            print(f"  - {iid} ...", end=" ", flush=True)
            # 옛 row 는 source_metadata 에 paper_keywords 가 없을 수도 — 안전 fallback.
            doc = ExtractedDoc(
                body=raw,
                title=title,
                abstract=None,                # backfill 은 항상 body 앞부분으로 cap.
                paper_keywords=meta.get("paper_keywords") or [],
            )
            res_text, res_tags = await _generate_and_save_summary(
                session, item_id=iid, doc=doc,
            )
            if res_text:
                print(f"OK ({len(res_text)} chars, tags={res_tags})")
                ok += 1
            else:
                print("실패")
                fail += 1
        print(f"\n완료: 성공 {ok} / 실패 {fail}")
        return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
