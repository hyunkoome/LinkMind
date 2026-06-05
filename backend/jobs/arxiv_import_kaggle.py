"""
Kaggle Cornell arXiv dataset(전체 2.7M 메타 JSONL) → arxiv_papers 대량 적재.

다운로드(사용자, ~4GB):
  kaggle datasets download -d Cornell-University/arxiv
  unzip arxiv.zip   # → arxiv-metadata-oai-snapshot.json (JSON lines)

적재:
  python -m backend.jobs.arxiv_import_kaggle <path/to/arxiv-metadata-oai-snapshot.json>
  python -m backend.jobs.arxiv_import_kaggle <path> --limit 100000   # 일부만(테스트)

성능 전략 (2.7M):
  - orjson 라인 스트리밍 파싱 (4GB 를 메모리에 안 올림).
  - asyncpg COPY 로 staging TEMP 적재(executemany 보다 10~100x) → INSERT…ON CONFLICT
    DO UPDATE(멱등 + 더 새 버전만). 배치(_BATCH)마다 flush.
  - GENERATED tsvector + GIN 은 최종 테이블 INSERT 시 계산/갱신됨(불가피하나 COPY→INSERT
    단일 경로라 행별 round-trip 없음).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import asyncpg
import orjson

from backend.config import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("arxiv_import_kaggle")

_BATCH = 50_000   # COPY → INSERT flush 단위

_STAGE_COLUMNS = [
    "arxiv_id", "title", "abstract", "authors", "categories",
    "version", "published", "updated", "doi", "journal_ref", "source",
]

_CREATE_STAGE = """
    CREATE TEMP TABLE _arxiv_stage (
        arxiv_id text, title text, abstract text, authors text[], categories text[],
        version text, published timestamptz, updated timestamptz,
        doi text, journal_ref text, source text
    ) ON COMMIT DROP
"""

# staging → 본 테이블. 더 새 버전(updated)만 갱신. fts_vector 는 GENERATED 라 자동.
_MERGE = """
    INSERT INTO arxiv_papers (
        arxiv_id, title, abstract, authors, categories, version,
        published, updated, doi, journal_ref, source
    )
    SELECT arxiv_id, title, abstract, authors, categories, version,
           published, updated, doi, journal_ref, source
    FROM _arxiv_stage
    ON CONFLICT (arxiv_id) DO UPDATE SET
        title=EXCLUDED.title, abstract=EXCLUDED.abstract, authors=EXCLUDED.authors,
        categories=EXCLUDED.categories, version=EXCLUDED.version,
        published=EXCLUDED.published, updated=EXCLUDED.updated,
        doi=EXCLUDED.doi, journal_ref=EXCLUDED.journal_ref,
        source=EXCLUDED.source, fetched_at=now()
    WHERE arxiv_papers.updated IS NULL OR EXCLUDED.updated IS NULL
       OR arxiv_papers.updated <= EXCLUDED.updated
"""


def _parse_created(s: str | None) -> datetime | None:
    """versions[0].created (RFC822: 'Mon, 2 Apr 2007 19:18:42 GMT') → datetime."""
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _parse_update_date(s: str | None) -> datetime | None:
    """update_date ('2008-11-26') → datetime."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def cornell_record_to_row(rec: dict[str, Any]) -> tuple | None:
    """Cornell JSON dict → arxiv_papers staging 튜플. title/id 없으면 None."""
    arxiv_id = (rec.get("id") or "").strip()
    title = " ".join((rec.get("title") or "").split())          # 개행·다중공백 축약
    if not arxiv_id or not title:
        return None
    abstract = " ".join((rec.get("abstract") or "").split())
    categories = (rec.get("categories") or "").split()
    versions = rec.get("versions") or []
    published = _parse_created(versions[0].get("created")) if versions else None
    updated = _parse_update_date(rec.get("update_date"))
    version = versions[-1].get("version") if versions else None
    # authors_parsed: [["Last","First",suffix], ...] → "First Last"
    authors = [
        f"{(p[1] or '').strip()} {(p[0] or '').strip()}".strip()
        for p in (rec.get("authors_parsed") or [])
        if isinstance(p, list) and p
    ]
    return (
        arxiv_id, title, abstract, authors, categories, version,
        published, updated, rec.get("doi") or None,
        rec.get("journal-ref") or None, "kaggle",
    )


async def _flush(conn: asyncpg.Connection, batch: list[tuple]) -> None:
    """배치를 staging COPY → MERGE → staging 비움."""
    if not batch:
        return
    await conn.execute("TRUNCATE _arxiv_stage")
    await conn.copy_records_to_table("_arxiv_stage", records=batch, columns=_STAGE_COLUMNS)
    await conn.execute(_MERGE)


async def run(path: str, *, limit: int | None = None) -> int:
    dsn = get_settings().effective_database_url.replace("+asyncpg", "")
    conn = await asyncpg.connect(dsn)
    total = 0
    try:
        # staging 은 세션 유지 동안 살아있게 ON COMMIT DROP 대신 명시 — 단일 connection.
        await conn.execute(_CREATE_STAGE.replace(" ON COMMIT DROP", ""))
        batch: list[tuple] = []
        with open(path, "rb") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                row = cornell_record_to_row(orjson.loads(line))
                if row is None:
                    continue
                batch.append(row)
                if len(batch) >= _BATCH:
                    await _flush(conn, batch)
                    total += len(batch)
                    batch = []
                    logger.info("  %d rows imported", total)
                    if limit and total >= limit:
                        break
        if batch and not (limit and total >= limit):
            await _flush(conn, batch)
            total += len(batch)
        logger.info("✅ Kaggle 적재 완료 — %d rows", total)
    finally:
        await conn.close()
    return total


def main() -> None:
    ap = argparse.ArgumentParser(description="Kaggle Cornell arXiv → arxiv_papers 적재")
    ap.add_argument("path", help="arxiv-metadata-oai-snapshot.json 경로")
    ap.add_argument("--limit", type=int, default=None, help="일부만 적재 (테스트)")
    args = ap.parse_args()
    asyncio.run(run(args.path, limit=args.limit))


if __name__ == "__main__":
    main()
