"""
VLLMEmbeddingProvider — vLLM 의 OpenAI-compatible /v1/embeddings 호출.

이 테스트는 HTTP mock (monkeypatch httpx.AsyncClient.post) 으로 vLLM 응답
시뮬레이션. 실제 vllm-embed 컨테이너는 필요 없음 → embedding marker 로 두지만
GPU/모델 의존성 없는 가벼운 테스트.

검증 포인트:
1. 정상 응답 → vectors 추출
2. index 가 섞여 와도 정렬 보장
3. 빈 입력 → empty result, HTTP 호출 X
4. 응답 차원이 self.dim 과 다르면 self.dim 자동 보정 (warning 만)
5. HTTP error → 예외 raise (raw 가 이미 commit 된 ingest_url 흐름에서
   _embed_and_index 실패가 위로 전파되어 backfill 가능하게 함)
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

pytestmark = pytest.mark.embedding


def _make_embedding_response(
    vectors: list[list[float]],
    model: str = "BAAI/bge-m3",
    indices: list[int] | None = None,
) -> dict[str, Any]:
    """OpenAI /v1/embeddings 응답 형식 — vLLM 도 동일."""
    n = len(vectors)
    if indices is None:
        indices = list(range(n))
    return {
        "object": "list",
        "data": [
            {"object": "embedding", "index": idx, "embedding": vec}
            for idx, vec in zip(indices, vectors)
        ],
        "model": model,
        "usage": {"prompt_tokens": n * 10, "total_tokens": n * 10},
    }


def _patch_post(monkeypatch, response_factory):
    """httpx.AsyncClient.post 를 monkeypatch — request 받아 response 반환.

    response_factory(request) -> httpx.Response. 예외 던지면 그대로 raise.
    """
    async def fake_post(self: httpx.AsyncClient, url: str, **kwargs: Any) -> httpx.Response:
        request = httpx.Request("POST", url, json=kwargs.get("json"))
        return response_factory(request, kwargs)

    monkeypatch.setattr("httpx.AsyncClient.post", fake_post)


def test_embed_normal_response(monkeypatch):
    """정상 응답 — vectors 가 잘 추출되고 모델/차원 보존."""
    from backend.embedding.vllm_embed import VLLMEmbeddingProvider

    expected_vectors = [[0.1, 0.2, 0.3, 0.4], [0.5, 0.6, 0.7, 0.8]]

    def factory(request, kwargs):
        # 요청 정확한지 verify
        payload = kwargs.get("json")
        assert payload["model"] == "BAAI/bge-m3"
        assert payload["input"] == ["text1", "text2"]
        return httpx.Response(
            200, json=_make_embedding_response(expected_vectors),
            request=request,
        )

    _patch_post(monkeypatch, factory)

    provider = VLLMEmbeddingProvider(
        base_url="http://fake:8002/v1",
        model_name="BAAI/bge-m3",
        dim=4,
    )
    result = asyncio.run(provider.embed(["text1", "text2"]))

    assert result.vectors == expected_vectors
    assert result.model == "BAAI/bge-m3"
    assert result.dim == 4


def test_embed_sorts_by_index(monkeypatch):
    """vLLM 응답이 index 섞여 와도 정렬해서 입력 순서 보장."""
    from backend.embedding.vllm_embed import VLLMEmbeddingProvider

    # 의도적으로 index 가 [2, 0, 1] 순으로 섞인 응답
    shuffled = [
        {"object": "embedding", "index": 2, "embedding": [3.0]},
        {"object": "embedding", "index": 0, "embedding": [1.0]},
        {"object": "embedding", "index": 1, "embedding": [2.0]},
    ]

    def factory(request, kwargs):
        return httpx.Response(
            200,
            json={"object": "list", "data": shuffled, "model": "m", "usage": {}},
            request=request,
        )

    _patch_post(monkeypatch, factory)

    provider = VLLMEmbeddingProvider(base_url="http://x/v1", model_name="m", dim=1)
    result = asyncio.run(provider.embed(["a", "b", "c"]))
    # 정렬 후 입력 순서대로 (index 0,1,2)
    assert result.vectors == [[1.0], [2.0], [3.0]]


def test_embed_empty_input_skips_http(monkeypatch):
    """빈 입력 → HTTP 호출 안 함 (vLLM 부하 회피, fast-path)."""
    from backend.embedding.vllm_embed import VLLMEmbeddingProvider

    called = {"n": 0}

    def factory(request, kwargs):
        called["n"] += 1
        return httpx.Response(200, json={}, request=request)

    _patch_post(monkeypatch, factory)

    provider = VLLMEmbeddingProvider(base_url="http://x/v1", model_name="m", dim=4)
    result = asyncio.run(provider.embed([]))

    assert result.vectors == []
    assert result.dim == 4
    assert called["n"] == 0, "빈 입력에서 HTTP 호출이 발생함"


def test_embed_corrects_mismatched_dim(monkeypatch, caplog):
    """응답 vector 차원이 self.dim 과 다르면 self.dim 자동 보정 + warning."""
    import logging
    from backend.embedding.vllm_embed import VLLMEmbeddingProvider

    # 응답 vector 가 6 차원인데 provider 는 dim=1024 로 init
    response_vec = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6]

    def factory(request, kwargs):
        return httpx.Response(
            200, json=_make_embedding_response([response_vec]), request=request,
        )

    _patch_post(monkeypatch, factory)

    provider = VLLMEmbeddingProvider(base_url="http://x/v1", model_name="m", dim=1024)
    with caplog.at_level(logging.WARNING, logger="backend.embedding.vllm_embed"):
        result = asyncio.run(provider.embed(["a"]))

    assert result.dim == 6  # 응답 차원으로 보정됨
    assert provider.dim == 6  # 이후 호출에도 보정값 유지
    assert any("응답 차원" in rec.message or "차원" in rec.message for rec in caplog.records)


def test_embed_propagates_http_error(monkeypatch):
    """HTTP 5xx 응답 — raise_for_status 가 예외 발생, 위로 전파."""
    from backend.embedding.vllm_embed import VLLMEmbeddingProvider

    def factory(request, kwargs):
        return httpx.Response(500, json={"error": "internal"}, request=request)

    _patch_post(monkeypatch, factory)

    provider = VLLMEmbeddingProvider(base_url="http://x/v1", model_name="m", dim=4)
    with pytest.raises(httpx.HTTPError):
        asyncio.run(provider.embed(["a"]))


def test_embed_path_appended_correctly(monkeypatch):
    """base_url 의 trailing slash 가 있어도 _EMBED_PATH 가 깔끔하게 붙음."""
    from backend.embedding.vllm_embed import VLLMEmbeddingProvider

    captured_url: dict[str, str] = {}

    def factory(request, kwargs):
        captured_url["url"] = str(request.url)
        return httpx.Response(
            200, json=_make_embedding_response([[1.0]]), request=request,
        )

    _patch_post(monkeypatch, factory)

    # trailing slash
    provider = VLLMEmbeddingProvider(
        base_url="http://x/v1/", model_name="m", dim=1,
    )
    asyncio.run(provider.embed(["a"]))
    assert captured_url["url"] == "http://x/v1/embeddings"

    # no trailing slash
    provider2 = VLLMEmbeddingProvider(
        base_url="http://x/v1", model_name="m", dim=1,
    )
    asyncio.run(provider2.embed(["a"]))
    assert captured_url["url"] == "http://x/v1/embeddings"


def test_factory_returns_vllm_provider(monkeypatch):
    """factory.get_embedding_provider() 가 settings.embedding_backend='vllm' 시
    VLLMEmbeddingProvider 반환."""
    from backend.embedding import factory as factory_mod
    from backend.embedding.vllm_embed import VLLMEmbeddingProvider

    # lru_cache 회피
    factory_mod.get_embedding_provider.cache_clear()

    settings = factory_mod.get_settings()
    monkeypatch.setattr(settings, "embedding_backend", "vllm")
    monkeypatch.setattr(settings, "embedding_model", "BAAI/bge-m3")
    monkeypatch.setattr(settings, "embedding_dim", 1024)

    provider = factory_mod.get_embedding_provider()
    assert isinstance(provider, VLLMEmbeddingProvider)
    assert provider.model == "BAAI/bge-m3"
    assert provider.dim == 1024

    factory_mod.get_embedding_provider.cache_clear()
