"""Anthropic Claude provider."""

from __future__ import annotations

from anthropic import AsyncAnthropic

from backend.llm.base import ChatMessage, LLMProvider, LLMResponse


class ClaudeProvider(LLMProvider):
    name = "claude"

    def __init__(self, api_key: str, default_model: str = "claude-haiku-4-5-20251001") -> None:
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY가 비어있습니다.")
        self._client = AsyncAnthropic(api_key=api_key)
        self._default_model = default_model

    async def chat(
        self,
        messages: list[ChatMessage],
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        # Anthropic API는 system을 분리, user/assistant만 messages에 둠.
        system_text = "\n".join(m.content for m in messages if m.role == "system") or None
        chat_msgs = [
            {"role": m.role, "content": m.content}
            for m in messages
            if m.role in ("user", "assistant")
        ]
        model_id = model or self._default_model
        resp = await self._client.messages.create(
            model=model_id,
            system=system_text,
            messages=chat_msgs,
            temperature=temperature,
            max_tokens=max_tokens or 4096,   # Anthropic은 max_tokens 필수
        )
        # content는 블록 리스트 — 텍스트 블록만 모아서 join
        text = "".join(
            block.text for block in resp.content if getattr(block, "type", None) == "text"
        )
        usage = {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens}
        return LLMResponse(text=text, model=model_id, provider=self.name, usage=usage)
