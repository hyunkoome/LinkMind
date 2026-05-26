"""
LinkMind D10 llm_wiki agents — backend 내부 multi-agent orchestration.

설계: docs/llm_wiki_design.md (2026-05-26)
패턴 차용:
  - physics-intern: state-centric architecture + BaseAgent ABC + template method
  - ml-intern: YAML prompt + sub-agent context 분리
  - karpathy llm_wiki: 3-layer (raw / wiki / schema), 3-op (ingest / query / lint)

agent 4종:
  - classifier — item → wiki_page (M:N) 자동 분류
  - retriever  — wiki_page 의 source items + 첨부 통합 (LLM 안 부름)
  - writer     — wiki_page body (markdown) 합성
  - critic     — citation 검증 + contradiction flag (wave-3)

§11 책임 분리: agent 는 backend 내부 모듈 — LLMProvider 직접 호출 OK.
  (ai_agents/ 와 다름. 그쪽은 외부 daemon 으로 backend HTTP 만 호출.)
"""

from __future__ import annotations

from backend.agents.base import (
    AgentBase,
    AgentContext,
    AgentResult,
    load_prompt,
)

__all__ = [
    "AgentBase",
    "AgentContext",
    "AgentResult",
    "load_prompt",
]
