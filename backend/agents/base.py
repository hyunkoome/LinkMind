"""
AgentBase ABC — D10 llm_wiki agent 공통 골격.

설계 원칙 (docs/llm_wiki_design.md §10.5):
  1. State-centric (physics-intern 패턴) — agent 내부에 conversation history X.
     매 run() 호출은 fresh context 를 DB state (wiki_pages / items / ...) 에서 build.
  2. Template method — sub-class 는 build_context() + invoke() 만 구현, base 가
     timing / agent_runs 적립 / 에러 wrapping 자동.
  3. YAML prompt (ml-intern 패턴) — prompts/<agent_name>_v<n>.yaml 시드.
     DB prompts 테이블에 import 되어 UI Settings 탭으로 편집 가능.
  4. async-first — FastAPI BackgroundTask 와 자연 호환.

agent_runs 적립:
  - 모든 run() 호출이 성공/실패 무관 agent_runs 에 1 row 적립.
  - input_full JSONB, output_text/output_meta, duration_ms, error 보존.
  - Phase 4 LoRA 학습 데이터의 핵심 신호 (§9 학습 후크).
"""

from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

import yaml
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("linkmind.agents")


# ============================================================================
# Data types
# ============================================================================

@dataclass
class AgentContext:
    """agent 한 번의 run() 호출에 주입되는 fresh state.

    state-centric — agent 내부에 누적 없음. 매번 새로 build.
    """

    session: AsyncSession                                  # DB session (자동 commit 안 함)
    related_item_id: UUID | None = None
    related_topic_id: UUID | None = None
    related_wiki_page_id: UUID | None = None
    extra: dict[str, Any] = field(default_factory=dict)    # agent-specific 추가 입력


@dataclass
class AgentResult:
    """agent run() 의 표준 응답."""

    agent_name: str
    agent_version: str
    llm_model: str | None
    output_text: str | None
    output_meta: dict[str, Any] | None = None
    duration_ms: int = 0
    error: str | None = None
    agent_run_id: UUID | None = None                       # agent_runs.id (적립 후 채워짐)

    @property
    def ok(self) -> bool:
        return self.error is None


# ============================================================================
# YAML prompt loader
# ============================================================================

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def load_prompt(name: str, version: str | None = None) -> dict[str, Any]:
    """YAML prompt 파일 로드. 시드용 — 실제 운영은 DB prompts 테이블 우선.

    파일 패턴: prompts/<name>_<version>.yaml (예: writer_v1.yaml).
    version 미지정 시 최신 v<숫자> 자동 선택.

    YAML 스키마:
        name: writer
        version: v1
        description: "..."
        system: |
          (system prompt 본문)
        user_template: |
          (user prompt template, jinja2-style {{ var }} 치환은 호출자가)
        output_format: "markdown" | "json" | "text"
    """
    if version:
        path = _PROMPTS_DIR / f"{name}_{version}.yaml"
        if not path.exists():
            raise FileNotFoundError(f"prompt 파일 없음: {path}")
    else:
        candidates = sorted(_PROMPTS_DIR.glob(f"{name}_v*.yaml"))
        if not candidates:
            raise FileNotFoundError(f"prompt 파일 없음: {name}_v*.yaml")
        path = candidates[-1]   # 가장 높은 버전

    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"prompt YAML 형식 오류: {path} (dict 아님)")
    return data


# ============================================================================
# agent_runs 적립
# ============================================================================

_INSERT_AGENT_RUN_SQL = text("""
    INSERT INTO agent_runs (
        agent_name, agent_version, llm_model,
        input_summary, input_full, output_text, output_meta,
        duration_ms, error,
        related_item_id, related_topic_id, related_wiki_page_id
    ) VALUES (
        :agent_name, :agent_version, :llm_model,
        :input_summary, CAST(:input_full AS JSONB), :output_text, CAST(:output_meta AS JSONB),
        :duration_ms, :error,
        :related_item_id, :related_topic_id, :related_wiki_page_id
    )
    RETURNING id
""")


async def _record_agent_run(
    session: AsyncSession,
    *,
    agent_name: str,
    agent_version: str,
    llm_model: str | None,
    input_summary: str | None,
    input_full: dict[str, Any] | None,
    output_text: str | None,
    output_meta: dict[str, Any] | None,
    duration_ms: int,
    error: str | None,
    related_item_id: UUID | None,
    related_topic_id: UUID | None,
    related_wiki_page_id: UUID | None,
) -> UUID:
    """agent_runs 에 1 row 적립. session.begin() 안에서 호출해도 안전."""
    row = (await session.execute(
        _INSERT_AGENT_RUN_SQL,
        {
            "agent_name": agent_name,
            "agent_version": agent_version,
            "llm_model": llm_model,
            "input_summary": input_summary,
            "input_full": json.dumps(input_full or {}, ensure_ascii=False, default=str),
            "output_text": output_text,
            "output_meta": json.dumps(output_meta or {}, ensure_ascii=False, default=str),
            "duration_ms": duration_ms,
            "error": error,
            "related_item_id": str(related_item_id) if related_item_id else None,
            "related_topic_id": str(related_topic_id) if related_topic_id else None,
            "related_wiki_page_id": str(related_wiki_page_id) if related_wiki_page_id else None,
        },
    )).first()
    return row[0]  # type: ignore[index]


# ============================================================================
# AgentBase ABC
# ============================================================================

class AgentBase(ABC):
    """모든 agent 의 공통 골격.

    sub-class 구현 책임:
      - agent_name (class attr)
      - agent_version (class attr) — prompt 버전과 동기
      - llm_model (class attr) — 사용하는 LLM ID (retriever 는 None)
      - async build_context(ctx) -> dict  — DB 에서 fresh input 조립
      - async invoke(input_payload) -> tuple[str | None, dict]
                                       — 실제 작업 수행 (LLM 호출 또는 순수 로직),
                                         (output_text, output_meta) 반환

    base 가 처리:
      - timing
      - try/except 로 에러 wrapping
      - agent_runs 적립
      - AgentResult 반환
    """

    agent_name: str = "base"
    agent_version: str = "v1"
    llm_model: str | None = None

    # run() 동안 stash — invoke / build_context 에서 self._ctx.session 등 접근.
    # state-centric: agent 인스턴스에 conversation history 는 안 누적,
    # 한 번의 run() 호출 동안의 ctx 만 일시 보관.
    _ctx: AgentContext | None = None

    @abstractmethod
    async def build_context(self, ctx: AgentContext) -> dict[str, Any]:
        """DB state 에서 fresh input 조립. state-centric 의 핵심.

        반환 dict 는 invoke() 의 input + agent_runs.input_full 에 그대로 적립.
        """
        ...

    @abstractmethod
    async def invoke(self, input_payload: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
        """실제 작업 수행. (output_text, output_meta) 반환.

        - LLM 호출 agent: output_text = LLM 응답, output_meta = parsed JSON 등
        - 순수 로직 agent (retriever): output_text = 짧은 summary, output_meta = 본 결과
        """
        ...

    def _input_summary(self, input_payload: dict[str, Any]) -> str | None:
        """디버그/인덱스 용 짧은 입력 summary. sub-class override 가능."""
        keys = sorted(input_payload.keys())[:5]
        return f"keys=[{', '.join(keys)}]"

    async def run(self, ctx: AgentContext) -> AgentResult:
        """template method — build_context → invoke → 적립.

        예외는 catch 해서 AgentResult.error 로 옮긴 후 적립 (agent_runs 에 실패도 trace).
        ctx 는 self._ctx 로 stash 되어 build_context/invoke 안에서 self._ctx.session 접근.
        """
        start = time.monotonic()
        input_payload: dict[str, Any] = {}
        output_text: str | None = None
        output_meta: dict[str, Any] | None = None
        error: str | None = None

        self._ctx = ctx
        try:
            try:
                input_payload = await self.build_context(ctx)
                output_text, output_meta = await self.invoke(input_payload)
            except Exception as exc:  # noqa: BLE001 — 모든 에러를 적립
                error = f"{type(exc).__name__}: {exc}"
                logger.exception(
                    "[%s/%s] agent run failed", self.agent_name, self.agent_version
                )
        finally:
            pass  # _ctx 는 적립 끝까지 유지 — 적립 후 None 으로

        duration_ms = int((time.monotonic() - start) * 1000)

        # agent_runs 적립 — 실패해도 trace 남김. session 은 호출자가 commit 책임.
        run_id: UUID | None = None
        try:
            run_id = await _record_agent_run(
                ctx.session,
                agent_name=self.agent_name,
                agent_version=self.agent_version,
                llm_model=self.llm_model,
                input_summary=self._input_summary(input_payload),
                input_full=input_payload,
                output_text=output_text,
                output_meta=output_meta,
                duration_ms=duration_ms,
                error=error,
                related_item_id=ctx.related_item_id,
                related_topic_id=ctx.related_topic_id,
                related_wiki_page_id=ctx.related_wiki_page_id,
            )
        except Exception as exc:  # noqa: BLE001
            # 적립 실패는 agent 결과에 영향 주지 X — 로그만.
            logger.error(
                "[%s/%s] agent_runs insert 실패 (계속): %s",
                self.agent_name, self.agent_version, exc,
            )

        result = AgentResult(
            agent_name=self.agent_name,
            agent_version=self.agent_version,
            llm_model=self.llm_model,
            output_text=output_text,
            output_meta=output_meta,
            duration_ms=duration_ms,
            error=error,
            agent_run_id=run_id,
        )
        self._ctx = None
        return result
