"""
LLM Provider factory.

설정에 따라 provider 인스턴스를 lazy하게 생성/캐시한다.
런타임 override (Settings 메뉴) 가 바뀌면 runtime_settings.update() 가
get_llm_provider.cache_clear() 를 불러서 다음 요청부터 새 값이 반영된다.
"""

from __future__ import annotations

from functools import lru_cache

from backend import runtime_settings
from backend.config import get_settings
from backend.llm.base import LLMProvider
from backend.llm.claude_provider import ClaudeProvider
from backend.llm.ollama_provider import OllamaProvider
from backend.llm.openai_provider import OpenAIProvider


@lru_cache(maxsize=4)
def get_llm_provider(name: str | None = None) -> LLMProvider:
    """provider 이름으로 인스턴스 반환. None이면 effective default 사용
    (runtime override → env 순서)."""
    settings = get_settings()
    chosen = (name or runtime_settings.get_effective_llm_provider()).lower()

    if chosen == "openai":
        return OpenAIProvider(
            api_key=settings.openai_api_key,
            default_model=runtime_settings.get_effective_openai_model(),
        )
    if chosen == "claude":
        return ClaudeProvider(
            api_key=settings.anthropic_api_key,
            default_model=runtime_settings.get_effective_anthropic_model(),
        )
    if chosen == "ollama":
        return OllamaProvider(
            base_url=settings.effective_ollama_base_url,
            default_model=runtime_settings.get_effective_ollama_model(),
        )
    raise ValueError(f"알 수 없는 LLM provider: {chosen}")
