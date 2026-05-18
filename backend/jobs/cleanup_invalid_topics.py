"""
DB 의 잘못된 topic 정리 (Phase 2.5 wave-3 follow-up).

배경:
- 이전 버그 (backend/utils/external_ids.py 의 GitHub regex 가 user-attachments
  같은 GitHub system path 도 owner/repo 로 인식) 로 인해 의미 없는 topic 들이
  자동 생성됨. 예: 'github:user-attachments/assets', 'github:orgs/something'.
- regex fix 후엔 새로 안 생기지만 기존 DB 의 row 는 그대로 — 이 스크립트가 정리.

동작:
1. topics 의 모든 slug 를 backend.utils.external_ids._is_valid_github_owner 로 검증
2. invalid 한 topic 찾음 → 그 topic 의 item_topics 먼저 삭제 (FK CASCADE 라 자동
   되지만 명시적 로깅 위해 분리) → topics row 삭제
3. dry-run default. --apply 옵션으로 실제 삭제.

사용:
    python -m backend.jobs.cleanup_invalid_topics            # dry-run (목록만)
    python -m backend.jobs.cleanup_invalid_topics --apply    # 실제 삭제

§2 가드레일: items / chunks / attachments 절대 안 건드림. topic ↔ item link 만
끊고 topic row 만 삭제. raw 데이터 손실 0.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.db.connection import get_engine
from backend.utils.external_ids import _is_valid_github_owner

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("linkmind.cleanup_topics")


def _is_invalid_topic_slug(slug: str | None) -> bool:
    """slug 가 잘못된 형식인지 — 현재는 GitHub 만 확인.

    Phase 3+ 에 다른 source (arxiv/doi/yt) 도 추가 가능.
    """
    if not slug:
        return True  # empty slug = invalid
    if slug.startswith("github:"):
        repo = slug.removeprefix("github:")
        # 'owner/repo' 형태여야 함
        if "/" not in repo:
            return True
        owner = repo.split("/", 1)[0]
        return not _is_valid_github_owner(owner)
    return False


async def main(apply: bool) -> None:
    engine = get_engine()
    SessionMaker = async_sessionmaker(engine, expire_on_commit=False)

    async with SessionMaker() as session:
        async with session.begin():
            res = await session.execute(
                text("SELECT id, slug, title, primary_external_id FROM topics ORDER BY slug"),
            )
            all_topics = list(res.mappings().all())
            logger.info("topics 총 %d 개", len(all_topics))

            invalid = [t for t in all_topics if _is_invalid_topic_slug(t["slug"])]
            if not invalid:
                logger.info("✅ 잘못된 topic 없음 — 정리할 게 없습니다")
                return

            logger.info("⚠️  잘못된 topic %d 개 발견:", len(invalid))
            for t in invalid:
                # 그 topic 의 item link 수도 확인
                link_count_row = await session.execute(
                    text("SELECT COUNT(*) FROM item_topics WHERE topic_id = :tid"),
                    {"tid": t["id"]},
                )
                link_count = link_count_row.scalar_one()
                logger.info(
                    "  - slug=%s title=%r item_links=%d",
                    t["slug"], t["title"], link_count,
                )

            if not apply:
                logger.info("")
                logger.info("dry-run 종료. 실제 삭제하려면:")
                logger.info("  python -m backend.jobs.cleanup_invalid_topics --apply")
                return

            # 실 삭제. item_topics 는 CASCADE 로 자동 정리되지만 명시 삭제 + log.
            deleted_links = 0
            for t in invalid:
                r = await session.execute(
                    text("DELETE FROM item_topics WHERE topic_id = :tid"),
                    {"tid": t["id"]},
                )
                deleted_links += r.rowcount or 0
            r2 = await session.execute(
                text("DELETE FROM topics WHERE id = ANY(:ids)"),
                {"ids": [t["id"] for t in invalid]},
            )
            deleted_topics = r2.rowcount or 0
            logger.info(
                "✅ 삭제 완료 — topics %d / item_topics %d",
                deleted_topics, deleted_links,
            )


if __name__ == "__main__":
    p = argparse.ArgumentParser(prog="cleanup_invalid_topics")
    p.add_argument("--apply", action="store_true",
                   help="실제 삭제 (기본 dry-run, 목록만)")
    args = p.parse_args()
    try:
        asyncio.run(main(apply=args.apply))
    except Exception as e:  # noqa: BLE001
        logger.error("실패: %s", e, exc_info=True)
        sys.exit(1)
