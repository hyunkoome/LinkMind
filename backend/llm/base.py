"""
LLM Provider 추상화.

목적:
  - OpenAI, Claude, Ollama 등을 동일 인터페이스로 호출
  - 향후 사용자가 학습시킨 sVLL을 새 Provider로 추가하기 쉽게

OpenClaw는 LLM provider가 아니라 client(에이전트)이므로 여기엔 없음.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass


@dataclass
class ChatMessage:
    role: str            # 'system' | 'user' | 'assistant'
    content: str


@dataclass
class LLMResponse:
    text: str
    model: str
    provider: str
    usage: dict | None = None    # 토큰 사용량 등 메타 (있으면)


class LLMProvider(ABC):
    """모든 LLM provider가 구현해야 하는 인터페이스."""

    name: str

    @abstractmethod
    async def chat(
        self,
        messages: list[ChatMessage],
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """대화형 응답 생성."""
        raise NotImplementedError

    async def stream_chat(
        self,
        messages: list[ChatMessage],
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        """대화형 응답을 토큰(델타) 단위로 stream.

        기본 구현은 streaming 미지원 provider 를 위한 fallback — chat() 으로 전체
        응답을 받은 뒤 한 덩어리로 yield 한다. 실제 token-by-token streaming 이
        필요한 provider(vLLM/OpenAI/Claude/Ollama)는 이 메서드를 override 한다.
        """
        resp = await self.chat(messages, model=model, temperature=temperature, max_tokens=max_tokens)
        yield resp.text

    async def summarize(self, text: str, instruction: str | None = None) -> str:
        """편의 메서드 — 요약. 모든 provider에서 동일하게 사용."""
        sys = instruction or (
            "You are a concise summarizer for technical research content. "
            "Summarize the input in 3-5 bullet points in Korean. "
            "Preserve technical terms (English) when appropriate."
        )
        resp = await self.chat([
            ChatMessage(role="system", content=sys),
            ChatMessage(role="user", content=text),
        ])
        return resp.text
