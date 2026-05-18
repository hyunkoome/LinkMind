"""vLLM provider — OpenAI 호환 endpoint 호출.

vLLM 은 self-hosted OpenAI 호환 server (https://docs.vllm.ai). 따라서 openai
SDK 그대로 사용, base_url 만 vLLM 서버로 변경. 인증은 default 로 없음 (LinkMind
는 같은 docker network 안의 trusted 통신).

Ollama 대비:
- 2-10x throughput (paged attention + continuous batching)
- RTX 4090 GPU utilization 좋음
- LoRA adapter hot-swap 지원 (Phase 4 sVLL 학습 후 즉시 serving)
- Qwen2.5 / Llama / Gemma / Qwen2-VL 등 mainstream 모델 풍부

LinkMind 의 LLMProvider abstraction 덕분에 Ollama ↔ vLLM swap 은 Settings 한 줄
변경 (default_llm_provider) 만으로 가능.
"""

from __future__ import annotations

from openai import AsyncOpenAI

from backend.llm.base import ChatMessage, LLMProvider, LLMResponse


class VLLMProvider(LLMProvider):
    name = "vllm"

    def __init__(self, base_url: str, default_model: str) -> None:
        """
        Args:
            base_url: vLLM 의 OpenAI 호환 endpoint (예: http://vllm:8000/v1)
            default_model: HF model id (예: Qwen/Qwen2.5-7B-Instruct).
                실행 중인 vLLM 서버가 그 모델로 시작됐어야 함.
        """
        # vLLM 은 default 로 auth 없음 — api_key 는 OpenAI SDK validation 통과용 dummy
        self._client = AsyncOpenAI(base_url=base_url, api_key="EMPTY")
        self._default_model = default_model

    async def chat(
        self,
        messages: list[ChatMessage],
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        model_id = model or self._default_model
        kwargs: dict = {
            "model": model_id,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        resp = await self._client.chat.completions.create(**kwargs)
        text = resp.choices[0].message.content or ""
        usage = None
        if resp.usage is not None:
            try:
                usage = resp.usage.model_dump()  # pydantic v2
            except AttributeError:
                usage = dict(resp.usage)  # type: ignore[arg-type]
        return LLMResponse(
            text=text,
            model=model_id,
            provider="vllm",
            usage=usage,
        )
