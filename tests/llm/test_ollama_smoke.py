"""
Ollama provider 실 호출 smoke — Ollama 컨테이너가 떠 있고 가벼운 모델이 pull
돼있을 때만 도는 e2e. 그 외엔 fixture 가 자동 pytest.skip.

CI 에서는 Ollama 서비스가 없으니 자동 skip → SKIP 으로 집계 (FAIL 아님).
로컬에서는 `bash scripts/tests/local/step4_llm.sh` 로 검증.

설계 의도:
- 짧은 prompt + max_tokens=10 으로 호출 시간 최소화 (~수초).
- 모델 list 에서 가장 가벼운 게 있으면 그것 사용. 사용자가 14b 만 가진 경우 그것도.
- 응답이 비어있지 않으면 PASS — 정답 검증은 안 함 (LLM 비결정성).
"""

from __future__ import annotations

import os

import httpx
import pytest

pytestmark = pytest.mark.llm


OLLAMA_BASE = os.getenv("OLLAMA_BASE_URL_LOCAL", "http://localhost:11434")


@pytest.fixture(scope="module")
def ollama_model() -> str:
    """Ollama health 체크 + 사용 가능한 모델 중 가장 가벼운 것 선택. 미가동/모델 없으면 skip."""
    try:
        r = httpx.get(f"{OLLAMA_BASE}/api/tags", timeout=3.0)
        if r.status_code != 200:
            pytest.skip(f"Ollama 응답 비정상 ({r.status_code})")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Ollama 미가동 ({OLLAMA_BASE}): {e}")
    body = r.json()
    models = body.get("models") or []
    if not models:
        pytest.skip("Ollama 에 pull 된 모델 없음 — `ollama pull qwen2.5:7b` 등")
    # 가장 가벼운 모델 우선 — size 가 작은 순.
    models_sorted = sorted(models, key=lambda m: m.get("size", 0))
    return models_sorted[0]["name"]


@pytest.mark.asyncio
async def test_ollama_provider_chat_returns_text(ollama_model: str):
    """짧은 한 줄 chat — 응답 string 이 비어있지 않은지만 검증."""
    from backend.llm.base import ChatMessage
    from backend.llm.ollama_provider import OllamaProvider

    provider = OllamaProvider(base_url=OLLAMA_BASE, default_model=ollama_model)
    try:
        resp = await provider.chat(
            [ChatMessage(role="user", content="Reply with the single word: ok")],
            max_tokens=10,
            temperature=0.0,
        )
    finally:
        await provider.aclose()

    assert resp.text, "Ollama 가 빈 응답 — 모델/Ollama 상태 점검"
    assert resp.provider == "ollama"
    assert resp.model == ollama_model


@pytest.mark.asyncio
async def test_ollama_provider_handles_system_message(ollama_model: str):
    """system + user 두 메시지 — Ollama 가 system role 을 거부하지 않는지."""
    from backend.llm.base import ChatMessage
    from backend.llm.ollama_provider import OllamaProvider

    provider = OllamaProvider(base_url=OLLAMA_BASE, default_model=ollama_model)
    try:
        resp = await provider.chat([
            ChatMessage(role="system", content="Always reply in one short word."),
            ChatMessage(role="user", content="Hi."),
        ], max_tokens=10, temperature=0.0)
    finally:
        await provider.aclose()

    assert resp.text
