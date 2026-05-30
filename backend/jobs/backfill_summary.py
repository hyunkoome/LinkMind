"""
backend/jobs/backfill_summary.py
----------------------------------------------------------------------------
items.summary IS NULL 인 row 의 raw_content 를 LLM 으로 요약해서 채움.
2026-05-23 부터: chunks (임베딩) 도 함께 보강 — 이전엔 summary 만 채워서
chunks 빠진 "반쪽" item 이 생기는 문제 해결. analysis_worker 와 동일 패턴.

이미 ingest 된 자료에 새 요약 로직을 소급 적용할 때 사용. 예:
  - ingest_url 에 요약 단계가 나중에 추가되어 기존 row 가 summary 없음
  - prompt 버전을 올린 뒤 전체 재요약 (--force 로)
  - GPU OOM 등으로 임베딩/요약 실패해 반쪽이 된 row 회복

사용:
    python -m backend.jobs.backfill_summary               # summary IS NULL 인 모든 item
    python -m backend.jobs.backfill_summary <item_id>     # 특정 item 1개
    python -m backend.jobs.backfill_summary --force       # summary 있어도 모두 재생성
    python -m backend.jobs.backfill_summary --only-foreign  # 중국어/일본어 섞인 summary 만 재생성
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any
from uuid import UUID


from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker  # noqa: E402

from backend import runtime_settings  # noqa: E402
from backend.db.connection import get_engine  # noqa: E402
from backend.ingest.url import ExtractedDoc, _embed_and_index, _generate_and_save_summary  # noqa: E402
from backend.utils.lang import (  # noqa: E402
    FOREIGN_CJK_SQL_PATTERN,
    FOREIGN_THRESHOLD,
    count_foreign_cjk,
)

logger = logging.getLogger("linkmind.backfill_summary")


async def _fetch_targets(
    session: AsyncSession, *, item_id: UUID | None, force: bool, only_foreign: bool,
) -> list[tuple[UUID, str, str, str | None, dict[str, Any]]]:
    """(item_id, source_type, raw_content, title, source_metadata) 튜플 목록."""
    if item_id is not None:
        rows = await session.execute(
            text("""
                SELECT id, source_type, raw_content, title, source_metadata
                FROM items WHERE id = :id
            """),
            {"id": str(item_id)},
        )
    elif only_foreign:
        # 중국어/일본어 섞인 summary 만 — SQL 로 한자/가나 1자+ 후보를 좁힌 뒤
        # Python count_foreign_cjk 로 threshold 초과만 정밀 선별 (Qwen 시절 잔재).
        rows = await session.execute(
            text(
                "SELECT id, source_type, raw_content, title, source_metadata, summary "
                "FROM items WHERE summary IS NOT NULL AND summary ~ :pat ORDER BY ingested_at"
            ),
            {"pat": FOREIGN_CJK_SQL_PATTERN},
        )
        return [
            (r.id, r.source_type, r.raw_content, r.title, r.source_metadata or {})
            for r in rows.all()
            if count_foreign_cjk(r.summary) > FOREIGN_THRESHOLD
        ]
    elif force:
        rows = await session.execute(text(
            "SELECT id, source_type, raw_content, title, source_metadata FROM items "
            "ORDER BY ingested_at"
        ))
    else:
        rows = await session.execute(text(
            "SELECT id, source_type, raw_content, title, source_metadata FROM items "
            "WHERE summary IS NULL ORDER BY ingested_at"
        ))
    return [
        (r.id, r.source_type, r.raw_content, r.title, r.source_metadata or {})
        for r in rows.all()
    ]


async def main() -> int:
    args = sys.argv[1:]
    force = "--force" in args
    only_foreign = "--only-foreign" in args
    args = [a for a in args if a not in ("--force", "--only-foreign")]
    item_id: UUID | None = UUID(args[0]) if args else None

    engine = get_engine()
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    # backend 가 안 떠 있어도 별도 프로세스로 도는 backfill 이라 prompt 캐시가 비어있다.
    # seed_and_load 로 DB → 캐시 적재 — 그래야 summary_prompt_version 이 정확히 기록됨.
    await runtime_settings.seed_and_load()

    async with session_factory() as session:
        targets = await _fetch_targets(
            session, item_id=item_id, force=force, only_foreign=only_foreign,
        )
        if not targets:
            print("대상 없음 (이미 모두 summary 보유, 또는 item 미존재).")
            return 0

        print(f"대상 {len(targets)} 건 — 임베딩 + 요약 보강 시작")
        ok, fail, chunks_added = 0, 0, 0
        for iid, source_type, raw, title, meta in targets:
            print(f"  - {iid} ({source_type}) ...", end=" ", flush=True)

            # 1. chunks 가 없으면 먼저 임베딩 (vllm-embed 통해 빠름, ~1-2초).
            #    이전 backfill 은 summary 만 채우고 chunks 는 안 채워서 "반쪽" 이 됐다.
            #    raw_content 너무 짧으면 chunks 자체가 의미 없어 skip.
            try:
                has_chunks = (await session.execute(
                    text("SELECT 1 FROM chunks WHERE item_id = :id LIMIT 1"),
                    {"id": iid},
                )).first() is not None
                if not has_chunks and raw and len(raw) >= 50:
                    n = await _embed_and_index(session, item_id=iid, text=raw)
                    chunks_added += n
                    print(f"chunks={n}", end=" ", flush=True)
            except Exception as e:  # noqa: BLE001
                # 임베딩 실패해도 summary 단계는 시도 — 둘은 독립.
                print(f"chunks-fail({type(e).__name__})", end=" ", flush=True)
                await session.rollback()

            # 2. summary 생성. PDF 는 abstract 재추출, 비-PDF 는 raw 그대로.
            abstract: str | None = None
            if source_type == "pdf":
                from backend.ingest.pdf import _detect_abstract
                abstract = _detect_abstract(raw)
            # 옛 row 는 source_metadata 에 paper_keywords 가 없을 수도 — 안전 fallback.
            doc = ExtractedDoc(
                body=raw,
                title=title,
                abstract=abstract,
                paper_keywords=meta.get("paper_keywords") or [],
            )
            try:
                res_text, res_tags = await _generate_and_save_summary(
                    session, item_id=iid, doc=doc,
                )
            except Exception as e:  # noqa: BLE001
                print(f"실패 ({type(e).__name__}: {e})")
                await session.rollback()
                fail += 1
                continue
            if res_text:
                print(f"OK ({len(res_text)} chars, tags={res_tags})")
                ok += 1
            else:
                print("summary 빈 응답")
                fail += 1
        print(f"\n완료: 성공 {ok} / 실패 {fail} / 누적 chunks {chunks_added}")
        return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
