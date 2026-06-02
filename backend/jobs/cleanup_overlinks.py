"""
D10.6 C (2026-06-02) — 본문-인용 over-link 정리 + self-wiki 보장.

**배경**: 옛 `wiki_backfill_from_topics` 잡이 item_topics 의 cross-modal 단서
(confidence 0.7 — 본문에 *링크된* 외부 ID) 를 wiki_page_items 로 그대로 승격
(`source='topic-inherited'`) 시켰다. 그 결과 예를 들어 "Becoming a Roboticist"
가이드가 본문에 `ethz-asl/kalibr` 를 *언급/링크* 했다는 이유만으로
`github__ethz-asl-kalibr` 위키의 'blog' 소스로 잘못 등록됐다 (주제 무관).

D10.6 A/A2 + 현재 classifier 는 이미 0.7 단서를 위키로 승격하지 않는다 (의미 매칭
`source='auto'` 만, role/confidence 부여). 즉 **신규 ingest 는 정상** — 이 잡은
그 전에 쌓인 레거시 over-link 만 청소한다:

  1) `source='topic-inherited' AND confidence < 0.9 AND user_action IS NULL` 인
     `wiki_page_items` 삭제 (본문-인용 over-link). `item_topics` 그래프 관계는 보존
     (별 테이블, CASCADE 아님) — "이 글이 그 repo 를 언급한다" 는 관계로는 남는다.
  2) 그 결과 위키가 0개로 남는 item 은 자기 정체성 self-wiki (`url__item__<uuid>`)
     를 생성/연결 (role='self', confidence=1.0, body_status='pending' → writer daemon
     이 본문 합성). "1 링크 = 1 위키" 원칙 복구.

idempotent — 재실행 안전 (이미 정리됐으면 0건). **dry-run 먼저.**

  python -m backend.jobs.cleanup_overlinks --dry-run   # 수치 + 샘플만
  python -m backend.jobs.cleanup_overlinks             # 실제 정리
  python -m backend.jobs.cleanup_overlinks --no-selfwiki  # over-link 삭제만 (self-wiki 생성 X)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.connection import close_engine, get_session_factory
from backend.utils.external_ids import extract_external_ids, native_identity_external_id
from backend.utils.wiki_slug import sanitize_wiki_slug

logger = logging.getLogger("linkmind.jobs.cleanup_overlinks")

# 본문-인용 over-link 판별 조건 (한 곳에서만 정의 — count/삭제 동일)
_OVERLINK_WHERE = (
    "source = 'topic-inherited' AND confidence < 0.9 AND user_action IS NULL"
)

_COUNT_SQL = text(f"""
    SELECT count(*) AS overlinks,
           count(DISTINCT item_id) AS items_affected,
           count(DISTINCT wiki_page_id) AS wikis_affected
    FROM wiki_page_items
    WHERE {_OVERLINK_WHERE}
""")

# 정리 후 위키가 0개로 남는 item (= 모든 멤버십이 over-link) → self-wiki 필요
_WIKILESS_ITEMS_SQL = text(f"""
    WITH bad AS (
        SELECT DISTINCT item_id FROM wiki_page_items WHERE {_OVERLINK_WHERE}
    )
    SELECT b.item_id FROM bad b
    WHERE NOT EXISTS (
        SELECT 1 FROM wiki_page_items w
        WHERE w.item_id = b.item_id
          AND NOT ({_OVERLINK_WHERE})
    )
""")

_FETCH_ITEMS_SQL = text("""
    SELECT id, title, source_url, source_type, summary FROM items WHERE id = ANY(:ids)
""")

_CREATE_WIKI_PAGE_SQL = text("""
    INSERT INTO wiki_pages (slug, title, description, body_status)
    VALUES (:slug, :title, :description, 'pending')
    ON CONFLICT (slug) DO UPDATE SET slug = EXCLUDED.slug
    RETURNING id
""")

_LINK_SQL = text("""
    INSERT INTO wiki_page_items (wiki_page_id, item_id, confidence, source, role)
    VALUES (:page_id, :item_id, 1.0, 'auto', :role)
    ON CONFLICT (wiki_page_id, item_id) DO UPDATE
        SET confidence = 1.0, source = 'auto', role = EXCLUDED.role
        WHERE wiki_page_items.user_action IS NULL
""")

_DELETE_OVERLINKS_SQL = text(f"DELETE FROM wiki_page_items WHERE {_OVERLINK_WHERE}")

# source / confidence / user_action 컬럼은 wiki_page_items 에만 존재 → join 에서도 unambiguous
_SAMPLE_SQL = text(f"""
    SELECT i.title, wp.slug
    FROM wiki_page_items wpi
    JOIN wiki_pages wp ON wp.id = wpi.wiki_page_id
    JOIN items i ON i.id = wpi.item_id
    WHERE {_OVERLINK_WHERE}
    LIMIT 10
""")


async def _ensure_self_wikis(session: AsyncSession, item_ids: list[str]) -> int:
    """위키가 0개로 남을 item 들에 self-wiki 생성/연결. 반환: 생성·연결 수."""
    if not item_ids:
        return 0
    rows = (await session.execute(
        _FETCH_ITEMS_SQL, {"ids": item_ids},
    )).mappings().all()
    linked = 0
    for r in rows:
        iid = str(r["id"])
        # native 정체성(자기 URL 기준) — github/arxiv/yt URL 이면 그 위키(primary),
        # 일반 블로그면 url:item self-wiki. (콘텐츠 추출 X — 자기 URL 만.)
        ids = extract_external_ids(url=r["source_url"]) if r["source_url"] else []
        native = native_identity_external_id(
            source_type=r["source_type"], url=r["source_url"], ids=ids,
        )
        if native is not None:
            slug, role = sanitize_wiki_slug(native.slug), "primary"
        else:
            slug, role = sanitize_wiki_slug(f"url:item:{iid}"), "self"
        title = r["title"] or slug
        desc = (r["summary"] or "")[:500] or None
        page_id = (await session.execute(_CREATE_WIKI_PAGE_SQL, {
            "slug": slug, "title": title, "description": desc,
        })).scalar()
        await session.execute(_LINK_SQL, {
            "page_id": str(page_id), "item_id": iid, "role": role,
        })
        linked += 1
    return linked


async def main(dry_run: bool, make_selfwiki: bool, limit: int | None) -> None:
    Session = get_session_factory()
    async with Session() as session:
        counts = (await session.execute(_COUNT_SQL)).mappings().first()
        wikiless = [str(r[0]) for r in (await session.execute(_WIKILESS_ITEMS_SQL)).all()]
        if limit:
            wikiless = wikiless[:limit]

        logger.info(
            "over-link %s개 (item %s, wiki %s) | 정리 후 위키 0 → self-wiki 필요: %s개",
            counts["overlinks"], counts["items_affected"],
            counts["wikis_affected"], len(wikiless),
        )

        if dry_run:
            samples = (await session.execute(_SAMPLE_SQL)).mappings().all()
            logger.info("[dry-run] 변경 없음. over-link 샘플 (자료 → 잘못 붙은 위키):")
            for s in samples:
                logger.info("  · %s  →  %s", (s["title"] or "")[:45], s["slug"])
            return

        created = 0
        if make_selfwiki:
            created = await _ensure_self_wikis(session, wikiless)
            logger.info("self-wiki 생성/연결: %s개 (body_status=pending → daemon 합성)", created)

        result = await session.execute(_DELETE_OVERLINKS_SQL)
        await session.commit()
        logger.info(
            "✅ over-link %s개 삭제, self-wiki %s개 생성. (item_topics 관계는 보존)",
            result.rowcount, created,
        )


async def _entry(dry_run: bool, make_selfwiki: bool, limit: int | None) -> None:
    try:
        await main(dry_run, make_selfwiki, limit)
    finally:
        await close_engine()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    parser = argparse.ArgumentParser(
        description="본문-인용 over-link 정리 + self-wiki 보장 (레거시 wiki_backfill_from_topics 청소)",
    )
    parser.add_argument("--dry-run", action="store_true", help="수치 + 샘플만, 변경 X")
    parser.add_argument("--no-selfwiki", action="store_true",
                        help="over-link 삭제만, 위키 없어지는 item 에 self-wiki 생성 X")
    parser.add_argument("--limit", type=int, default=None, help="self-wiki 생성 N건만 (디버깅)")
    args = parser.parse_args()
    asyncio.run(_entry(args.dry_run, not args.no_selfwiki, args.limit))
