"""
backend/jobs/mark_fetch_failed.py
----------------------------------------------------------------------------
"정리 필요한 자료" 자동 분류 + 마커 표준화.

D12 (placeholder UI) 의 backend 토대. 옛 데이터 중 본문 추출 실패 / 짧은
raw / 이미지 OCR 미처리 자료를 분류하고 ``source_metadata.fetch_error_kind``
필드로 표준화한다. frontend cleanup 페이지가 이 분류를 기반으로 필터링.

분류 (우선순위 순):

1. ``extraction_failed`` — URL ingest 의 _save_url_only fallback (이미
   source_metadata.fetch_error string 마킹돼 있음). kind 만 보강.
2. ``image_no_ocr`` — source_type='document' + raw_content 가
   '[binary file: ... image/...]' placeholder. Phase 3 OCR 영역.
3. ``binary_no_extract`` — '[binary file: ...]' placeholder 인데 이미지 아닌
   바이너리 (압축/실행파일 등).
4. ``short_raw`` — summary IS NULL + length(raw_content) < 200 인 잡탕.
   주로 URL 페이지가 거의 비어있던 경우.

기존 fetch_error 인프라와 호환 — kind 는 추가 metadata 일 뿐, fetch_error
string 은 그대로.

사용:
    python -m backend.jobs.mark_fetch_failed                # 실제 UPDATE
    python -m backend.jobs.mark_fetch_failed --dry-run      # 분류만, UPDATE 없음
"""
from __future__ import annotations

import asyncio
import logging
import sys
from collections import Counter
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.connection import get_engine

logger = logging.getLogger("linkmind.mark_fetch_failed")


def classify_item(
    source_type: str,
    raw_content: str | None,
    summary: str | None,
    source_metadata: dict[str, Any] | None,
) -> str | None:
    """item 한 건을 fetch_error_kind 분류. 정상이면 None.

    Pure function — 테스트 가능. DB / IO 없음.
    """
    meta = source_metadata or {}
    raw = raw_content or ""
    has_summary = bool(summary and summary.strip())

    # 0. 이미 kind 마킹된 row 는 재마킹 안 함 (idempotent — 재실행 안전).
    if meta.get("fetch_error_kind"):
        return None

    # 1. URL ingest 의 _save_url_only 흐름 — 이미 fetch_error string 마킹됨.
    #    kind 없으면 extraction_failed 로 표준화.
    if meta.get("fetch_error"):
        return "extraction_failed"

    # 정상 자료: summary 있고 raw 충분 → skip
    if has_summary and len(raw) >= 200:
        return None

    # 2. 텔레그램 이미지 첨부 등 — raw 가 '[binary file:' placeholder.
    if raw.startswith("[binary file: ") and "image/" in raw:
        return "image_no_ocr"
    if raw.startswith("[binary file: "):
        return "binary_no_extract"

    # 3. 그 외 짧은 raw — summary 없거나 본문 거의 빈 경우.
    #    summary 있으면 (예: 메모) 짧아도 OK — cleanup 대상 아님.
    if not has_summary and len(raw) < 200:
        return "short_raw"

    return None


async def fetch_candidates(
    session: AsyncSession,
) -> list[tuple[str, str, str, str | None, dict[str, Any]]]:
    """잠재 cleanup 대상 후보 (kind 없거나 정상 marker 없는 row)."""
    rows = await session.execute(text("""
        SELECT id::text, source_type, raw_content, summary, source_metadata
        FROM items
        WHERE
          source_metadata::jsonb->>'fetch_error_kind' IS NULL
          AND (
            (summary IS NULL OR summary = '')
            OR length(raw_content) < 200
            OR source_metadata::jsonb ? 'fetch_error'
          )
    """))
    return [
        (r.id, r.source_type, r.raw_content or "", r.summary, r.source_metadata or {})
        for r in rows.all()
    ]


async def _apply_one(
    session: AsyncSession, item_id: str, kind: str, marked_at: str,
) -> None:
    """단건 UPDATE — jsonb merge."""
    await session.execute(
        text("""
            UPDATE items
            SET source_metadata = source_metadata::jsonb || jsonb_build_object(
                'fetch_error_kind', CAST(:kind AS text),
                'fetch_error_marked_at', CAST(:marked_at AS text)
            )
            WHERE id = :id
        """),
        {"kind": kind, "marked_at": marked_at, "id": UUID(item_id)},
    )


async def run(*, dry_run: bool = False) -> dict[str, int]:
    engine = get_engine()
    session_factory = async_sessionmaker(
        engine, expire_on_commit=False, class_=AsyncSession,
    )
    counts: Counter[str] = Counter()
    marked_at = datetime.now(timezone.utc).isoformat()

    async with session_factory() as session:
        candidates = await fetch_candidates(session)
        for item_id, source_type, raw, summary, meta in candidates:
            kind = classify_item(source_type, raw, summary, meta)
            if kind is None:
                counts["skip"] += 1
                continue
            counts[kind] += 1
            if not dry_run:
                await _apply_one(session, item_id, kind, marked_at)
        if not dry_run:
            await session.commit()
    return dict(counts)


async def main() -> int:
    args = sys.argv[1:]
    dry_run = "--dry-run" in args

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print(f"분류 시작 (dry_run={dry_run}) ...")
    counts = await run(dry_run=dry_run)

    print("\n분류 결과:")
    total_marked = 0
    for kind, n in sorted(counts.items(), key=lambda x: -x[1]):
        marker = "•" if kind != "skip" else " "
        print(f"  {marker} {kind:24s} {n:6d}")
        if kind != "skip":
            total_marked += n
    print(f"\n총 마킹 대상: {total_marked} 건"
          + (" (dry-run — DB 미변경)" if dry_run else " — DB 갱신 완료"))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
