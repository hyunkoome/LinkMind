"""
scripts/generate_topic_descriptions.py
----------------------------------------------------------------------------
각 topic 의 자식 item summary 들을 LLM 으로 합성해 `topics.description` 에 저장.

흐름:
1. items 가 2개 이상 묶인 topic 만 후보 (single-item topic 은 그냥 그 item summary
   가 곧 topic 의 설명이라 LLM 비용 절약).
2. 각 자식 item 의 (role, title, summary) 를 합쳐 LLM 에 전달 — '이 topic 은 무엇인가?
   각 modality 가 어떤 관점에서 같은 주제를 다루는지' 한국어 5-8 bullet 으로 요약.
3. 결과를 topics.description 에 UPDATE. summary_model 캐시 (Versioned analysis
   원칙에 가깝게 — model 추적은 source_metadata 가 아니라 description 끝의
   주석으로 표기).

사용:
    python scripts/generate_topic_descriptions.py             # description IS NULL 인 topic 만
    python scripts/generate_topic_descriptions.py <slug>      # 특정 topic 1개
    python scripts/generate_topic_descriptions.py --force     # 모두 재생성
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker  # noqa: E402

from backend import runtime_settings  # noqa: E402
from backend.db.connection import get_engine  # noqa: E402
from backend.db.repository import list_items_for_topic  # noqa: E402
from backend.llm.base import ChatMessage  # noqa: E402
from backend.llm.factory import get_llm_provider  # noqa: E402


_TOPIC_SYSTEM_PROMPT = """\
너는 LinkMind 의 'topic 합성 요약' 어시스턴트다.

입력은 같은 주제로 자동 그룹핑된 여러 item 들의 (모달리티, 제목, 요약) 묶음이다.
모달리티는 paper / pdf / code / video / playlist / blog / note 중 하나.

출력 규칙:
- 무조건 한국어. 기술 용어/모델명/약어/고유명사만 원문(영어) 유지.
- 첫 줄: 이 topic 의 한 줄 요약 (제목 또는 핵심 contribution).
- 그 다음 5~8개 bullet: 어떤 modality 가 어떤 관점에서 이 topic 을 다루는지.
  예: "- [paper] arxiv 2106.09685 — LoRA 원논문, 저순위 적응 기법 제안",
      "- [code] microsoft/LoRA — PyTorch 구현, Apache 2.0, 다양한 backbone 지원",
      "- [video] PYr-LSOf2OY — Gaussian Splatting 튜토리얼".
- 마지막에 # 으로 시작하는 해시태그 줄 (5-10개) — paper id / 핵심 모델 / 분야.
- markdown 의 굵게/이탤릭/표 사용 금지. 평문 bullet 만.
"""


async def _topics_to_process(
    session: AsyncSession, *, slug: str | None, force: bool,
) -> list[tuple[Any, str, str | None]]:
    """(topic_id, slug, title) 목록. items 가 2개 이상이고 description NULL 인 것 (force 아니면)."""
    if slug:
        rows = await session.execute(
            text("""
                SELECT t.id, t.slug, t.title FROM topics t
                WHERE t.slug = :slug
            """),
            {"slug": slug},
        )
    else:
        where_extra = "" if force else " AND t.description IS NULL"
        rows = await session.execute(
            text(f"""
                SELECT t.id, t.slug, t.title FROM topics t
                JOIN item_topics it ON it.topic_id = t.id
                WHERE TRUE {where_extra}
                GROUP BY t.id
                HAVING COUNT(it.item_id) >= 2
                ORDER BY t.updated_at DESC
            """),
        )
    return [(r.id, r.slug, r.title) for r in rows.all()]


def _format_items(items: list[dict[str, Any]]) -> str:
    """LLM 에 줄 입력 — modality 별 묶음."""
    lines: list[str] = []
    for it in items:
        title = (it.get("title") or "(no title)")[:120]
        role = it.get("role") or it.get("source_type")
        summary = (it.get("summary") or "(no summary)").strip()
        # 너무 길지 않게
        if len(summary) > 1500:
            summary = summary[:1500] + " ..."
        lines.append(f"\n[{role}] {title}\n{summary}\n")
    return "\n---\n".join(lines)


async def main() -> int:
    args = sys.argv[1:]
    force = "--force" in args
    args = [a for a in args if a != "--force"]
    slug = args[0] if args else None

    engine = get_engine()
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    await runtime_settings.seed_and_load()
    llm = get_llm_provider()

    async with session_factory() as session:
        targets = await _topics_to_process(session, slug=slug, force=force)
        if not targets:
            print("대상 없음 (description 모두 보유, 또는 자식 item 2개 미만).")
            return 0

        print(f"대상 {len(targets)} 건 — topic description 생성 시작")
        ok, fail = 0, 0
        for tid, t_slug, t_title in targets:
            items = await list_items_for_topic(session, topic_id=tid)
            if len(items) < 2:
                continue
            user_input = (
                f"# Topic: {t_slug}  (title: {t_title})\n\n"
                f"이 topic 에 묶인 {len(items)}개의 item:\n"
                + _format_items(items)
            )
            try:
                resp = await llm.chat([
                    ChatMessage(role="system", content=_TOPIC_SYSTEM_PROMPT),
                    ChatMessage(role="user", content=user_input),
                ])
            except Exception as e:  # noqa: BLE001
                print(f"  - {t_slug} ... 실패 {type(e).__name__}: {e}")
                fail += 1
                continue
            description = (
                resp.text.strip()
                + f"\n\n<!-- generated by {resp.provider}/{resp.model} -->"
            )
            await session.execute(
                text("UPDATE topics SET description = :d WHERE id = :id"),
                {"d": description, "id": tid},
            )
            await session.commit()
            print(f"  - {t_slug} ... OK ({len(resp.text)} chars)")
            ok += 1

        print(f"\n완료: 성공 {ok} / 실패 {fail}")
        return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
