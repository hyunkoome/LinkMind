"""OpenAI Chat Completions provider."""

from __future__ import annotations

from openai import AsyncOpenAI

from backend.llm.base import ChatMessage, LLMProvider, LLMResponse


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, api_key: str, default_model: str = "gpt-4o-mini") -> None:
        if not api_key:
            raise ValueError("OPENAI_API_KEY가 비어있습니다.")
        self._client = AsyncOpenAI(api_key=api_key)
        self._default_model = default_model

    async def chat(
        self,
        messages: list[ChatMessage],
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        model_id = model or self._default_model
        resp = await self._client.chat.completions.create(
            model=model_id,
            messages=[{"role": m.role, "content": m.content} for m in messages],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        text = resp.choices[0].message.content or ""
        usage = resp.usage.model_dump() if resp.usage else None
        return LLMResponse(text=text, model=model_id, provider=self.name, usage=usage)
