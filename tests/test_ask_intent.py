"""
backend.api.ask._detect_intent 의 agentic 라우팅 검증 — LLM 응답을 가짜 provider 로
대체. 네트워크/DB 없이 intent 판정 + arxiv 검색어 추출 로직만 확인.

확인:
- arxiv_search 판정 + 영어 검색어 추출
- rag 판정 (외부 검색 아님)
- 코드 fence 로 감싼 JSON 도 파싱
- 파싱 불가/예외 → ('rag', '') graceful fallback
- arxiv_search 인데 query 비면 원 질문으로 fallback
"""

from __future__ import annotations

import pytest

from backend.api import ask as ask_module
from backend.llm.base import LLMResponse


class _FakeProvider:
    name = "fake"

    def __init__(self, reply: str = "", raise_exc: Exception | None = None):
        self._reply = reply
        self._raise = raise_exc

    async def chat(self, messages, model=None, temperature=0.2, max_tokens=None):
        if self._raise is not None:
            raise self._raise
        return LLMResponse(text=self._reply, model="fake-model", provider="fake")


@pytest.mark.asyncio
async def test_detect_intent_arxiv_search():
    provider = _FakeProvider('{"intent": "arxiv_search", "arxiv_query": "diffusion models"}')
    intent, query = await ask_module._detect_intent("최신 디퓨전 논문 찾아줘", [], provider, None)
    assert intent == "arxiv_search"
    assert query == "diffusion models"


@pytest.mark.asyncio
async def test_detect_intent_rag():
    provider = _FakeProvider('{"intent": "rag", "arxiv_query": ""}')
    intent, query = await ask_module._detect_intent("내가 저장한 자료 요약해줘", [], provider, None)
    assert intent == "rag"
    assert query == ""


@pytest.mark.asyncio
async def test_detect_intent_json_in_code_fence():
    provider = _FakeProvider('```json\n{"intent": "arxiv_search", "arxiv_query": "vision transformer"}\n```')
    intent, query = await ask_module._detect_intent("ViT 논문 검색", [], provider, None)
    assert intent == "arxiv_search"
    assert query == "vision transformer"


@pytest.mark.asyncio
async def test_detect_intent_unparseable_falls_back_to_rag():
    provider = _FakeProvider("이건 JSON 이 아니라 그냥 잡담입니다")
    intent, query = await ask_module._detect_intent("뭐 좀 알려줘", [], provider, None)
    assert intent == "rag"
    assert query == ""


@pytest.mark.asyncio
async def test_detect_intent_exception_falls_back_to_rag():
    provider = _FakeProvider(raise_exc=RuntimeError("LLM down"))
    intent, query = await ask_module._detect_intent("아무거나", [], provider, None)
    assert intent == "rag"
    assert query == ""


@pytest.mark.asyncio
async def test_detect_intent_arxiv_search_empty_query_uses_question():
    provider = _FakeProvider('{"intent": "arxiv_search", "arxiv_query": ""}')
    intent, query = await ask_module._detect_intent("Mamba 논문 찾아줘", [], provider, None)
    assert intent == "arxiv_search"
    # 검색어를 못 뽑으면 원 질문으로 fallback 검색.
    assert query == "Mamba 논문 찾아줘"


def test_arxiv_context_block_formats_papers():
    papers = [
        {"title": "LoRA", "authors": ["A", "B"], "published": "2021",
         "abs_url": "https://arxiv.org/abs/2106.09685", "summary": "low rank"},
    ]
    ctx = ask_module._arxiv_context(papers)
    assert "[1] LoRA" in ctx
    assert "arxiv 검색 결과" in ctx


def test_arxiv_context_block_empty():
    assert "검색 결과 없음" in ask_module._arxiv_context([])
