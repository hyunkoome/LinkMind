"""
LLM Provider factory.

설정에 따라 provider 인스턴스를 lazy하게 생성/캐시한다.
"""

from __future__ import annotations

from functools import lru_cache

from backend.config import get_settings
from backend.llm.base import LLMProvider
from backend.llm.claude_provider import ClaudeProvider
from backend.llm.ollama_provider import OllamaProvider
from backend.llm.openai_provider import OpenAIProvider


@lru_cache(maxsize=4)
def get_llm_provider(name: str | None = None) -> LLMProvider:
    """provider 이름으로 인스턴스 반환. None이면 default 사용."""
    settings = get_settings()
    chosen = (name or settings.default_llm_provider).lower()

    if chosen == "openai":
        return OpenAIProvider(
            api_key=settings.openai_api_key,
            default_model=settings.openai_model,
        )
    if chosen == "claude":
        return ClaudeProvider(
            api_key=settings.anthropic_api_key,
            default_model=settings.anthropic_model,
        )
    if chosen == "ollama":
        return OllamaProvider(
            base_url=settings.ollama_base_url,
            default_model=settings.ollama_model,
        )
    raise ValueError(f"알 수 없는 LLM provider: {chosen}")
