"""
D10 wave-1g-0 (2026-05-26) — 기존 topics 23,940 → wiki_pages 1:1 옮김.

배경 (사용자 통찰):
  - LinkMind 는 이미 16,463 items / 23,940 topics / 29,225 item_topics 누적
  - 새 wiki 체계 (wiki_pages + wiki_page_items) 가 빈 상태
  - classifier 가 16,463 items 백필하려면 ~9시간 + LLM 비용 — 비효율
  - 대신 **topics 가 이미 의미 단위 클러스터** (arxiv:2106.09685 등 external_id 기반
    신뢰성) → wiki_pages 로 1:1 옮김이 자연

설계:
  1. topics → wiki_pages (1:1)
     - slug, title, description 그대로
     - body_status='ready' (다음 사용자 GET 시 lazy 합성)
     - 이미 wiki_page 있으면 skip (idempotent)
  2. item_topics → wiki_page_items
     - role, confidence 그대로
     - source='topic-inherited' (classifier 가 아닌 옮긴 거임 명시)
  3. classifier 는 **신규 ingest** 부터 자동 hook (wave-2)
  4. 사용자가 새로 만든 wiki 페이지나 신규 자료의 cross-cutting link 는 classifier 가
     점진 보강

사용:
  python -m backend.jobs.wiki_backfill_from_topics --dry-run     # 검증만
  python -m backend.jobs.wiki_backfill_from_topics                # 실행
  python -m backend.jobs.wiki_backfill_from_topics --limit 100    # 일부만
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from typing import Any

from sqlalchemy import text

from backend.db.connection import close_engine, get_session_factory

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s : %(message)s",
)
logger = logging.getLogger("linkmind.jobs.wiki_backfill")


# ─────────────────────────────────────────────────────────────────────────────
# SQL
# ─────────────────────────────────────────────────────────────────────────────

# topics 중 아직 wiki_pages 에 매핑 안 된 것만 (idempotent)
_FETCH_PENDING_TOPICS_SQL = text("""
    SELECT t.id AS topic_id, t.slug, t.title, t.description, t.tags
    FROM topics t
    LEFT JOIN wiki_pages wp ON wp.topic_id = t.id
    WHERE wp.id IS NULL
    ORDER BY t.created_at ASC
    LIMIT :limit
""")


_COUNT_PENDING_SQL = text("""
    SELECT COUNT(*) FROM topics t
    LEFT JOIN wiki_pages wp ON wp.topic_id = t.id
    WHERE wp.id IS NULL
""")


_INSERT_WIKI_PAGE_SQL = text("""
    INSERT INTO wiki_pages (topic_id, slug, title, description, body_status)
    VALUES (:topic_id, :slug, :title, :description, 'ready')
    ON CONFLICT (slug) DO UPDATE
        SET topic_id = COALESCE(wiki_pages.topic_id, EXCLUDED.topic_id)
    RETURNING id
""")


# item_topics → wiki_page_items 옮김 (idempotent — ON CONFLICT DO NOTHING)
_LINK_ITEMS_SQL = text("""
    INSERT INTO wiki_page_items (wiki_page_id, item_id, confidence, source, role)
    SELECT
        :page_id, it.item_id, it.confidence, 'topic-inherited', it.role
    FROM item_topics it
    WHERE it.topic_id = :topic_id
    ON CONFLICT (wiki_page_id, item_id) DO NOTHING
""")


_STATS_SQL = text("""
    SELECT
        (SELECT COUNT(*) FROM topics) AS topics,
        (SELECT COUNT(*) FROM wiki_pages) AS wiki_pages,
        (SELECT COUNT(*) FROM item_topics) AS item_topics,
        (SELECT COUNT(*) FROM wiki_page_items) AS wiki_page_items
""")


# ─────────────────────────────────────────────────────────────────────────────
# slug sanitize
# ─────────────────────────────────────────────────────────────────────────────

def _sanitize_slug(raw: str) -> str:
    """topics.slug 가 'arxiv:2106.09685' 같이 콜론 포함 — wiki_pages.slug (URL key) 로
    안전한 형태로. URL path 에 들어가도 OK 형태."""
    if not raw:
        return "untitled"
    # 콜론 → '__', 공백/슬래시 → '-', 그 외 [a-z0-9._-] 만 허용
    s = raw.strip().lower()
    s = s.replace(":", "__").replace("/", "-").replace(" ", "-")
    # 허용 문자만 (한글은 wiki 페이지 URL 로 쓰기 좋게 보존)
    import re
    s = re.sub(r"[^a-z0-9가-힣._\-]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-_")
    return s[:200] if s else "untitled"


# ─────────────────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────────────────

async def main(dry_run: bool, limit: int) -> None:
    session_factory = get_session_factory()

    # 사전 통계
    async with session_factory() as session:
        async with session.begin():
            stats = (await session.execute(_STATS_SQL)).mappings().first()
            pending = (await session.execute(_COUNT_PENDING_SQL)).scalar()
    logger.info(
        "=== 사전 상태 — topics=%d wiki_pages=%d item_topics=%d wiki_page_items=%d pending_topics=%d",
        stats["topics"], stats["wiki_pages"],
        stats["item_topics"], stats["wiki_page_items"], pending,
    )

    if dry_run:
        logger.info("--- DRY RUN: 처리 안 함 ---")
        async with session_factory() as session:
            async with session.begin():
                rows = (await session.execute(
                    _FETCH_PENDING_TOPICS_SQL, {"limit": min(limit, 10)},
                )).mappings().all()
        logger.info("처리할 topic 샘플 (최대 10건):")
        for r in rows:
            sanitized = _sanitize_slug(r["slug"])
            logger.info("  • topic_id=%s slug='%s' → wiki_slug='%s' title=%s",
                        r["topic_id"], r["slug"], sanitized, (r["title"] or "")[:60])
        await close_engine()
        return

    # 실 실행 — batch 단위로 처리 (한 batch = 500 topics)
    BATCH_SIZE = 500
    total_processed = 0
    total_skipped_slug_conflict = 0
    total_linked_items = 0

    while total_processed < limit:
        remaining = limit - total_processed
        batch_limit = min(BATCH_SIZE, remaining)

        async with session_factory() as session:
            async with session.begin():
                rows = (await session.execute(
                    _FETCH_PENDING_TOPICS_SQL, {"limit": batch_limit},
                )).mappings().all()

                if not rows:
                    logger.info("처리할 topic 없음 — backfill 완료")
                    break

                for r in rows:
                    raw_slug = r["slug"] or str(r["topic_id"])
                    wiki_slug = _sanitize_slug(raw_slug)
                    title = r["title"] or wiki_slug
                    description = r["description"]

                    try:
                        insert_result = await session.execute(_INSERT_WIKI_PAGE_SQL, {
                            "topic_id": str(r["topic_id"]),
                            "slug": wiki_slug,
                            "title": title,
                            "description": description,
                        })
                        page_row = insert_result.first()
                        if not page_row:
                            total_skipped_slug_conflict += 1
                            continue
                        page_id = str(page_row[0])

                        # item_topics → wiki_page_items 옮김
                        link_result = await session.execute(_LINK_ITEMS_SQL, {
                            "page_id": page_id,
                            "topic_id": str(r["topic_id"]),
                        })
                        total_linked_items += (link_result.rowcount or 0)
                        total_processed += 1

                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "topic 처리 실패 (slug=%s, %s: %s)",
                            raw_slug, type(exc).__name__, exc,
                        )
                        total_skipped_slug_conflict += 1

        logger.info(
            "batch 완료 — total processed=%d, linked items=%d, skipped=%d",
            total_processed, total_linked_items, total_skipped_slug_conflict,
        )

    # 사후 통계
    async with session_factory() as session:
        async with session.begin():
            stats = (await session.execute(_STATS_SQL)).mappings().first()
            pending = (await session.execute(_COUNT_PENDING_SQL)).scalar()
    logger.info(
        "=== 사후 상태 — topics=%d wiki_pages=%d item_topics=%d wiki_page_items=%d pending=%d",
        stats["topics"], stats["wiki_pages"],
        stats["item_topics"], stats["wiki_page_items"], pending,
    )
    logger.info(
        "✅ backfill 완료 — 신규 wiki_pages=%d, link 된 items=%d, skip(slug 충돌)=%d",
        total_processed, total_linked_items, total_skipped_slug_conflict,
    )
    await close_engine()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="처리 안 하고 sample 만")
    ap.add_argument("--limit", type=int, default=100000, help="처리할 topic 갯수 한계")
    args = ap.parse_args()
    asyncio.run(main(dry_run=args.dry_run, limit=args.limit))
