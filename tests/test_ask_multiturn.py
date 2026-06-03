"""ask 멀티턴 단위 테스트 — history 메시지 조립 + 후속질문 쿼리 재작성(condense)
+ SSE 프레임 포맷 + streaming fallback.

순수 함수 / mock provider 만 사용 (DB·네트워크 없음) → cpu 카테고리 (마커 없음).
멀티턴 도입(2026-06-03)에 동반.
"""

from __future__ import annotations

import json

import pytest

from backend.api.ask import (
    _MAX_HISTORY_TURNS,
    _build_messages,
    _condense_query,
    _sse,
)
from backend.llm.base import ChatMessage, LLMProvider, LLMResponse
from backend.schemas.models import AskTurn


# ── _build_messages — system + history + (context+질문) 조립 ──


def test_build_messages_no_history():
    msgs = _build_messages("SYS", [], "CTX", "질문?")
    assert [m.role for m in msgs] == ["system", "user"]
    assert msgs[0].content == "SYS"
    assert "[Context]\nCTX" in msgs[1].content
    assert "[Question]\n질문?" in msgs[1].content


def test_build_messages_includes_history_in_order():
    history = [
        AskTurn(role="user", content="첫 질문"),
        AskTurn(role="assistant", content="첫 답변"),
    ]
    msgs = _build_messages("SYS", history, "CTX", "후속 질문")
    assert [m.role for m in msgs] == ["system", "user", "assistant", "user"]
    assert msgs[1].content == "첫 질문"
    assert msgs[2].content == "첫 답변"
    # 마지막 user 턴은 현재 질문 + context 가 새로 붙음 (history 의 user 턴과 구분)
    assert "후속 질문" in msgs[3].content
    assert "[Context]\nCTX" in msgs[3].content


def test_build_messages_caps_history():
    # _MAX_HISTORY_TURNS 초과분은 잘려 최근 것만 — context 토큰 폭증 방지.
    history = [
        AskTurn(role="user", content=f"q{i}") for i in range(_MAX_HISTORY_TURNS + 5)
    ]
    msgs = _build_messages("SYS", history, "CTX", "현재")
    # system(1) + 최근 N history + 현재 user(1)
    assert len(msgs) == 1 + _MAX_HISTORY_TURNS + 1
    history_contents = [m.content for m in msgs[1:-1]]
    assert "q0" not in history_contents          # 가장 오래된 건 빠짐
    assert f"q{_MAX_HISTORY_TURNS + 4}" in history_contents  # 최신은 포함


# ── _condense_query — 후속질문 → 독립형 검색 쿼리 ──


class _StubProvider(LLMProvider):
    """LLM 호출을 흉내내는 stub — chat() 만 구현, stream_chat 은 base fallback 사용."""

    name = "stub"

    def __init__(self, text: str = "재작성된 쿼리", raise_exc: bool = False) -> None:
        self._text = text
        self._raise = raise_exc
        self.called = False

    async def chat(self, messages, model=None, temperature=0.2, max_tokens=None):
        self.called = True
        if self._raise:
            raise RuntimeError("LLM down")
        return LLMResponse(text=self._text, model="m", provider="stub")


@pytest.mark.asyncio
async def test_condense_skips_llm_when_no_history():
    prov = _StubProvider()
    out = await _condense_query("원 질문", [], prov, None)
    assert out == "원 질문"
    assert prov.called is False  # 첫 턴 — 불필요한 LLM 호출 안 함 (지연 회피)


@pytest.mark.asyncio
async def test_condense_rewrites_followup():
    prov = _StubProvider(text="LoRA 파인튜닝 방법")
    history = [
        AskTurn(role="user", content="LoRA 가 뭐야"),
        AskTurn(role="assistant", content="LoRA 는 ..."),
    ]
    out = await _condense_query("그거 어떻게 해?", history, prov, None)
    assert out == "LoRA 파인튜닝 방법"
    assert prov.called is True


@pytest.mark.asyncio
async def test_condense_takes_first_line_only():
    prov = _StubProvider(text="첫 줄 쿼리\n불필요한 설명 줄")
    history = [AskTurn(role="user", content="x")]
    out = await _condense_query("후속", history, prov, None)
    assert out == "첫 줄 쿼리"


@pytest.mark.asyncio
async def test_condense_falls_back_to_question_on_error():
    prov = _StubProvider(raise_exc=True)
    history = [AskTurn(role="user", content="x")]
    out = await _condense_query("원 질문", history, prov, None)
    assert out == "원 질문"  # 재작성 실패는 치명적 아님 — 원 질문으로 검색


@pytest.mark.asyncio
async def test_condense_empty_response_falls_back():
    prov = _StubProvider(text="   ")
    history = [AskTurn(role="user", content="x")]
    out = await _condense_query("원 질문", history, prov, None)
    assert out == "원 질문"


# ── SSE 프레임 포맷 ──


def test_sse_frame_format_and_korean_preserved():
    frame = _sse({"type": "token", "text": "안녕하세요"})
    assert frame.startswith("data: ")
    assert frame.endswith("\n\n")
    payload = json.loads(frame[len("data: "):].strip())
    assert payload["type"] == "token"
    # ensure_ascii=False — 한국어가 \uXXXX 로 escape 되지 않고 원문 보존
    assert payload["text"] == "안녕하세요"
    assert "\\u" not in frame


# ── base.stream_chat fallback — streaming 미지원 provider 는 전체를 한 번에 yield ──


@pytest.mark.asyncio
async def test_stream_chat_fallback_yields_full_text():
    prov = _StubProvider(text="전체 답변 텍스트")
    chunks = [
        c async for c in prov.stream_chat([ChatMessage(role="user", content="x")])
    ]
    assert "".join(chunks) == "전체 답변 텍스트"
