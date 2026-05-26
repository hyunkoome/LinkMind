"""
D10 wave-1 smoke — 기존 LoRA topic 위에 수동 wiki_page 만들고
retriever + writer agent end-to-end 실행.

검증:
  1. wiki_pages INSERT (slug='lora-fine-tuning-smoke')
  2. item_topics → wiki_page_items 수동 link (그 topic 의 모든 items)
  3. RetrieverAgent.run() → output_meta 확인 + agent_runs 적립 확인
  4. WriterAgent.run() → vLLM 호출 → markdown body 생성
  5. wiki_pages.body / wiki_page_versions / agent_runs 확인
  6. 결과 본문 출력

cleanup:
  - 끝에 wiki_pages.id 출력 → 재실행 가능 (UPDATE 만 됨, INSERT 는 idempotent)
  - 실제 DELETE 하려면 `--cleanup` 옵션
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from uuid import UUID

from sqlalchemy import text

from backend.agents import AgentContext
from backend.agents.retriever import RetrieverAgent
from backend.agents.writer import WriterAgent
from backend.db.connection import get_session_factory

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("smoke.d10")


SMOKE_SLUG = "lora-fine-tuning-smoke"
SMOKE_TITLE = "LoRA Fine-tuning (smoke)"
SMOKE_DESCRIPTION = "D10 wave-1 smoke — Low-Rank Adaptation 의 핵심 기법 정리"

# arxiv:2106.09685 (LoRA 원 논문) topic 의 id — 위 SQL 으로 확인
LORA_TOPIC_ID = UUID("ad084778-b760-40bd-96a4-140da4310e07")


async def upsert_wiki_page(session, topic_id: UUID, slug: str, title: str, description: str) -> UUID:
    """slug 가 이미 있으면 그 id 반환, 없으면 INSERT."""
    existing = (await session.execute(
        text("SELECT id FROM wiki_pages WHERE slug = :slug"), {"slug": slug},
    )).scalar_one_or_none()
    if existing:
        logger.info("wiki_pages 이미 있음: id=%s slug=%s", existing, slug)
        return existing
    row = (await session.execute(
        text("""
            INSERT INTO wiki_pages (topic_id, slug, title, description, body_status)
            VALUES (:topic_id, :slug, :title, :description, 'empty')
            RETURNING id
        """),
        {
            "topic_id": str(topic_id),
            "slug": slug,
            "title": title,
            "description": description,
        },
    )).first()
    page_id = row[0]
    logger.info("wiki_pages 신규: id=%s slug=%s", page_id, slug)
    return page_id


async def link_topic_items(session, page_id: UUID, topic_id: UUID) -> int:
    """item_topics 의 row 들을 wiki_page_items 로 복사 (idempotent — ON CONFLICT DO NOTHING)."""
    result = await session.execute(text("""
        INSERT INTO wiki_page_items (wiki_page_id, item_id, confidence, source, role)
        SELECT
            :page_id, it.item_id, it.confidence, 'topic-inherited', it.role
        FROM item_topics it
        WHERE it.topic_id = :topic_id
        ON CONFLICT (wiki_page_id, item_id) DO NOTHING
    """), {
        "page_id": str(page_id),
        "topic_id": str(topic_id),
    })
    return result.rowcount or 0


async def fetch_body(session, page_id: UUID) -> dict:
    row = (await session.execute(text("""
        SELECT body, body_model, body_prompt_version, body_status, body_generated_at,
               (SELECT MAX(version_number) FROM wiki_page_versions WHERE page_id = :page_id) AS latest_version
        FROM wiki_pages WHERE id = :page_id
    """), {"page_id": str(page_id)})).mappings().first()
    return dict(row) if row else {}


async def main(cleanup: bool) -> None:
    session_factory = get_session_factory()

    async with session_factory() as session:
        async with session.begin():
            # 1) wiki_pages
            page_id = await upsert_wiki_page(
                session, LORA_TOPIC_ID, SMOKE_SLUG, SMOKE_TITLE, SMOKE_DESCRIPTION,
            )
            # 2) wiki_page_items
            linked = await link_topic_items(session, page_id, LORA_TOPIC_ID)
            logger.info("wiki_page_items 신규 link: %d", linked)

        # 3) RetrieverAgent
        async with session.begin():
            ctx = AgentContext(session=session, related_wiki_page_id=page_id)
            retriever = RetrieverAgent()
            retr_result = await retriever.run(ctx)
        logger.info(
            "[retriever] ok=%s duration_ms=%s output_text=%s",
            retr_result.ok, retr_result.duration_ms, retr_result.output_text,
        )
        if not retr_result.ok:
            logger.error("retriever 실패: %s", retr_result.error)
            sys.exit(1)
        wiki_ctx = retr_result.output_meta
        logger.info(
            "wiki context — sources=%d, attachments=%d, cross_links=%d",
            len(wiki_ctx["sources"]),
            wiki_ctx["attachment_count"],
            len(wiki_ctx["cross_link_candidates"]),
        )

        # 4) WriterAgent — vLLM 호출 (수 초 ~ 수십 초)
        logger.info("vLLM 호출 시작 (시간 걸림)...")
        async with session.begin():
            ctx = AgentContext(
                session=session,
                related_wiki_page_id=page_id,
                related_topic_id=LORA_TOPIC_ID,
                extra={"trigger_reason": "first_gen"},
            )
            writer = WriterAgent()
            wr_result = await writer.run(ctx)
        logger.info(
            "[writer] ok=%s duration_ms=%s",
            wr_result.ok, wr_result.duration_ms,
        )
        if not wr_result.ok:
            logger.error("writer 실패: %s", wr_result.error)
            sys.exit(1)
        logger.info(
            "writer output_meta — body_length=%s version=%s source_count=%s",
            wr_result.output_meta.get("body_length"),
            wr_result.output_meta.get("version_number"),
            wr_result.output_meta.get("source_count"),
        )

        # 5) DB 상태 검증
        body_state = await fetch_body(session, page_id)
        logger.info(
            "DB 상태 — status=%s version=%s model=%s prompt_v=%s generated_at=%s",
            body_state.get("body_status"),
            body_state.get("latest_version"),
            body_state.get("body_model"),
            body_state.get("body_prompt_version"),
            body_state.get("body_generated_at"),
        )

        # agent_runs 확인
        runs = (await session.execute(text("""
            SELECT agent_name, agent_version, llm_model, duration_ms, error,
                   length(output_text) AS output_len
            FROM agent_runs
            WHERE related_wiki_page_id = :page_id
            ORDER BY created_at DESC LIMIT 5
        """), {"page_id": str(page_id)})).mappings().all()
        logger.info("agent_runs (최근 5건):")
        for r in runs:
            logger.info(
                "  %s/%s model=%s duration_ms=%s error=%s output_len=%s",
                r["agent_name"], r["agent_version"], r["llm_model"],
                r["duration_ms"], r["error"], r["output_len"],
            )

        # 6) 본문 출력
        print("\n========== GENERATED BODY ==========\n")
        print(body_state.get("body") or "(empty)")
        print("\n========== END ==========\n")

        if cleanup:
            async with session.begin():
                await session.execute(text("DELETE FROM wiki_pages WHERE id = :pid"),
                                      {"pid": str(page_id)})
            logger.info("cleanup: wiki_pages DELETED (CASCADE — versions/items 자동 삭제)")
        else:
            logger.info("재실행 가능 — slug=%s page_id=%s", SMOKE_SLUG, page_id)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--cleanup", action="store_true", help="끝에 wiki_pages DELETE")
    args = ap.parse_args()
    asyncio.run(main(cleanup=args.cleanup))
