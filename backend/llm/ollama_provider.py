"""
Ollama provider — 로컬 LLM (Docker 컨테이너에서 GPU로 실행).

Ollama는 OpenAI 호환 엔드포인트도 제공하지만, native API를 직접 호출하는 게
모델 관리(`/api/show`, `/api/pull`)와 일관성 있어서 선호.
"""

from __future__ import annotations

import httpx

from backend.llm.base import ChatMessage, LLMProvider, LLMResponse


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(self, base_url: str, default_model: str = "llama3.2:latest") -> None:
        self._base_url = base_url.rstrip("/")
        self._default_model = default_model
        # Ollama 응답은 GPU 모델 로드 등으로 첫 호출이 느릴 수 있어 넉넉히.
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(120.0))

    async def chat(
        self,
        messages: list[ChatMessage],
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        model_id = model or self._default_model
        payload: dict = {
            "model": model_id,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": False,
            "options": {"temperature": temperature},
        }
        if max_tokens is not None:
            payload["options"]["num_predict"] = max_tokens

        r = await self._client.post(f"{self._base_url}/api/chat", json=payload)
        r.raise_for_status()
        data = r.json()
        text = data.get("message", {}).get("content", "")
        usage = {
            "prompt_eval_count": data.get("prompt_eval_count"),
            "eval_count": data.get("eval_count"),
        }
        return LLMResponse(text=text, model=model_id, provider=self.name, usage=usage)

    async def aclose(self) -> None:
        await self._client.aclose()
