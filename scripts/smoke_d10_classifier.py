"""
D10 wave-1d smoke — ClassifierAgent 검증.

시나리오:
  - 기존 wiki: "lora-fine-tuning-smoke" (wave-1c smoke 가 만든 페이지)
  - test item 1: LoRA 논문 URL (강 매칭 기대)
  - test item 2: ComfyUI LoRA YouTube (중 매칭 + 새 페이지 제안 기대)

검증:
  - LLM JSON output parse 성공
  - matched list 가 정확
  - new_pages 제안 가 의미 있음
  - wiki_page_items INSERT 성공
  - 매칭된 wiki_pages.body_status='stale'
  - agent_runs 적립
"""

from __future__ import annotations

import asyncio
import logging
import sys
from uuid import UUID

from sqlalchemy import text

from backend.agents import AgentContext
from backend.agents.classifier import ClassifierAgent
from backend.db.connection import get_session_factory

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("smoke.d10.classifier")


TEST_CASES = [
    ("LoRA 논문 URL (강 매칭 기대)", UUID("d1656303-efe0-497c-9d76-4f81cf0b5008")),
    ("ComfyUI LoRA YouTube (중 매칭 + 새 페이지 기대)", UUID("35730a18-ee45-42c3-ae68-39aac761d026")),
]


async def main() -> None:
    session_factory = get_session_factory()

    async with session_factory() as session:
        # 사전 상태 — wiki_pages 갯수
        async with session.begin():
            before_pages = (await session.execute(text("SELECT COUNT(*) FROM wiki_pages"))).scalar()
        logger.info("=== 사전 상태: wiki_pages=%d ===", before_pages)

        for label, item_id in TEST_CASES:
            logger.info("\n========== %s ==========", label)
            logger.info("item_id=%s", item_id)

            async with session.begin():
                ctx = AgentContext(session=session, related_item_id=item_id)
                clf = ClassifierAgent()
                result = await clf.run(ctx)

            logger.info(
                "[classifier] ok=%s duration_ms=%s output_text=%s",
                result.ok, result.duration_ms, result.output_text,
            )
            if not result.ok:
                logger.error("classifier 실패: %s", result.error)
                continue

            meta = result.output_meta or {}
            logger.info("matched_count=%s new_pages_count=%s",
                        meta.get("matched_count"), meta.get("new_pages_count"))
            logger.info("linked_page_ids=%s", meta.get("linked_page_ids"))
            logger.info("created_pages=%s", meta.get("created_pages"))
            logger.info("skipped_low_conf=%s", meta.get("skipped_low_conf"))
            logger.info("reasoning: %s", (meta.get("reasoning") or "")[:300])

            # 현재 link 된 wiki_pages 확인
            async with session.begin():
                links = (await session.execute(text("""
                    SELECT wp.slug, wp.title, wpi.confidence, wpi.role, wpi.source, wp.body_status
                    FROM wiki_page_items wpi
                    JOIN wiki_pages wp ON wp.id = wpi.wiki_page_id
                    WHERE wpi.item_id = :iid
                    ORDER BY wpi.confidence DESC
                """), {"iid": str(item_id)})).mappings().all()
            logger.info("이 item 의 wiki 매핑 (%d 개):", len(links))
            for l in links:
                logger.info("  • %s (%s) conf=%.2f role=%s source=%s body=%s",
                            l["slug"], l["title"], float(l["confidence"]),
                            l["role"], l["source"], l["body_status"])

        # 사후 상태
        async with session.begin():
            after_pages = (await session.execute(text("SELECT COUNT(*) FROM wiki_pages"))).scalar()
            stale_count = (await session.execute(text(
                "SELECT COUNT(*) FROM wiki_pages WHERE body_status = 'stale'"
            ))).scalar()
        logger.info("\n=== 사후 상태: wiki_pages=%d (신규 +%d), stale=%d ===",
                    after_pages, after_pages - before_pages, stale_count)


if __name__ == "__main__":
    asyncio.run(main())
