"""
backend/jobs/backfill_description_from_tldr.py
─────────────────────────────────────────────
기존 wiki_pages 의 title / description 을 body 에서 파싱해 정제 (사용자 명시 2026-05-30).

배경:
  - title: 옛날엔 raw item title(SNS 제목 + 해시태그 + "댓글 N" 등 지저분)을 복사.
    writer 는 body 의 `# 헤더`에 핵심 제목(예: "CLOC")을 이미 만든다 → 그걸 title 로.
  - description: 옛날엔 item summary(Qwen 시절 중국어 섞임) 복사. body 의 TL;DR(`> ...`)
    로 통일 → 리스트 카드 미리보기가 한국어.

writer 는 이제 생성 시 자동으로 title/description 을 채우고(이 job 은 1회성 기존 데이터
정리), LLM 호출 없이 body 파싱만 하므로 빠르다 (수초). idempotent — 재실행 안전.

실행:
  python -m backend.jobs.backfill_description_from_tldr --dry-run        # 미리보기
  python -m backend.jobs.backfill_description_from_tldr                  # title + description 통일
  bash scripts/run_description_backfill.sh [--dry-run]                   # shell 래퍼
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import text

from backend.agents.writer import _extract_title, _extract_tldr
from backend.db.connection import close_engine, get_session_factory

_FETCH_SQL = text("""
    SELECT id, slug, title, description, body
    FROM wiki_pages
    WHERE body_status = 'completed' AND body IS NOT NULL
""")

# COALESCE — body 에서 못 뽑으면(None) 기존 값 유지.
_UPDATE_SQL = text("""
    UPDATE wiki_pages
    SET title = COALESCE(:title, title),
        description = COALESCE(:description, description)
    WHERE id = :page_id
""")


async def main(*, dry_run: bool) -> None:
    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(_FETCH_SQL)).mappings().all()

    title_upd = 0
    desc_upd = 0
    samples: list[str] = []

    async with sf() as s:
        async with s.begin():
            for r in rows:
                new_title = _extract_title(r["body"])
                new_tldr = _extract_tldr(r["body"])
                # 실제 바뀔 값만 (같으면 None 으로 둬서 COALESCE 가 기존 유지)
                title_arg = new_title if (new_title and new_title != (r["title"] or "")) else None
                desc_arg = new_tldr if (new_tldr and new_tldr != (r["description"] or "")) else None
                if title_arg is None and desc_arg is None:
                    continue
                if title_arg:
                    title_upd += 1
                if desc_arg:
                    desc_upd += 1
                if len(samples) < 6 and title_arg:
                    samples.append(f"  옛 제목: {(r['title'] or '')[:55]}\n  새 제목: {title_arg[:55]}")
                if not dry_run:
                    await s.execute(_UPDATE_SQL, {
                        "title": title_arg, "description": desc_arg, "page_id": str(r["id"]),
                    })

    print(f"\n전체 completed 위키: {len(rows)}")
    print(f"{'[DRY RUN] ' if dry_run else ''}title 갱신: {title_upd} | description 갱신: {desc_upd}")
    if samples:
        print("\n제목 변경 sample:")
        for s_ in samples:
            print(s_)
    if dry_run:
        print("\n--- DRY RUN: 실제 변경 안 함 ---")
    await close_engine()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="wiki title/description 을 body 에서 파싱해 정제")
    ap.add_argument("--dry-run", action="store_true", help="미리보기, 변경 안 함")
    args = ap.parse_args()
    asyncio.run(main(dry_run=args.dry_run))
