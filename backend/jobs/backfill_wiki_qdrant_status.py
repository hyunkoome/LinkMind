"""
backfill_wiki_qdrant_status — Qdrant linkmind_wiki_pages 의 payload.body_status 를
Postgres wiki_pages.body_status 와 동기화.

배경 (2026-06-04 버그 fix):
  writer.py 가 Qdrant payload 에 body_status='ready' 로 잘못 넣어왔다. Postgres 는
  'completed' 인데. ask.py 의 _retrieve_wikis(status_filter=['completed']) 가 'ready'
  와 안 맞아 위키 본문 검색이 항상 0건 → 하이브리드 RAG 에서 위키가 통째로 빠졌다.
  writer.py 는 'completed' 로 고쳤고, 이 job 은 이미 인덱싱된 기존 포인트를 일괄 보정.

idempotent: 여러 번 돌려도 안전 (Postgres 값을 그대로 미러). Postgres 를 source of
truth 로 — 미래에 status 가 추가로 어긋나도 이 job 으로 재동기화.

  python -m backend.jobs.backfill_wiki_qdrant_status            # 실제 적용
  python -m backend.jobs.backfill_wiki_qdrant_status --dry-run  # 미리보기
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from qdrant_client.http import models as qmodels
from sqlalchemy import text

from backend.db.connection import get_engine
from backend.embedding.qdrant_store import get_qdrant_client
from backend.embedding.wiki_qdrant import WIKI_COLLECTION

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("linkmind.jobs.backfill_wiki_qdrant_status")

_BATCH = 500


async def main(dry_run: bool = False) -> None:
    engine = get_engine()
    async with engine.connect() as conn:
        rows = (await conn.execute(
            text("SELECT id, body_status FROM wiki_pages"),
        )).all()
    # body_status 별로 page_id 묶기 — 같은 값끼리 batch set_payload.
    by_status: dict[str, list[str]] = {}
    for pid, status in rows:
        by_status.setdefault(status, []).append(str(pid))

    total = len(rows)
    logger.info("Postgres wiki_pages %d개. body_status 분포: %s",
                total, {k: len(v) for k, v in by_status.items()})
    if dry_run:
        logger.info("[dry-run] Qdrant payload 를 위 분포대로 set_payload 할 예정 (적용 안 함).")
        return

    client = get_qdrant_client()
    updated = 0
    for status, ids in by_status.items():
        for i in range(0, len(ids), _BATCH):
            batch = ids[i:i + _BATCH]
            await client.set_payload(
                collection_name=WIKI_COLLECTION,
                payload={"body_status": status},
                points=batch,
                wait=True,
            )
            updated += len(batch)
            logger.info("  set body_status=%r : %d/%d", status, updated, total)
    logger.info("완료 — %d개 포인트 payload 동기화.", updated)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    asyncio.run(main(dry_run=args.dry_run))
