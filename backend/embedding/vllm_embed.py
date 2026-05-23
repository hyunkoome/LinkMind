"""vLLM-based embedding (OpenAI-compatible /v1/embeddings).

vLLM 0.21+ 의 `--runner pooling` 모드로 띄운 embedding 서버를 HTTP 로 호출.
LinkMind 의 모든 임베딩 호출 (uvicorn / watcher / CLI) 이 이 서버를 공유 →
GPU 에 모델 1번만 로드, OOM 회피. CLAUDE.md §13 D13.

vLLM 의 OpenAI-compatible `/v1/embeddings` endpoint 응답 형식:
    {
        "object": "list",
        "data": [
            {"object": "embedding", "index": 0, "embedding": [0.1, 0.2, ...]},
            {"object": "embedding", "index": 1, "embedding": [...]},
        ],
        "model": "BAAI/bge-m3",
        "usage": {"prompt_tokens": N, "total_tokens": N}
    }

bge-m3 의 pooling/normalize 는 vLLM 의 BgeM3EmbeddingModel 이 sentence-transformers
와 동일하게 처리 (mean pooling + L2 normalize) — 기존 인덱스와 vector 호환.
"""

from __future__ import annotations

import logging

import httpx

from backend.embedding.base import EmbeddingProvider, EmbeddingResult

logger = logging.getLogger(__name__)

# vLLM-embed default timeout. bge-m3 inference 자체는 빠르지만 첫 요청 시
# cudagraph warmup 으로 시간 듦. 큰 batch 도 여유.
_DEFAULT_TIMEOUT = 60.0
# base_url 이 "/v1" 까지 포함되어 있다고 가정 (기존 vllm_base_url 패턴 일관).
# 예: "http://vllm-embed:8000/v1" + "/embeddings" → "/v1/embeddings"
_EMBED_PATH = "/embeddings"


class VLLMEmbeddingProvider(EmbeddingProvider):
    """vLLM serve --runner pooling 으로 띄운 embedding 서버 HTTP 클라이언트."""

    name = "vllm"

    def __init__(
        self,
        *,
        base_url: str,
        model_name: str,
        dim: int,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self.model = model_name
        self.dim = dim
        self._timeout = timeout
        # 차원 검증은 첫 embed() 호출 시 응답으로 수행 — 서버 미가동/모델 미로드 상태에선
        # init 단계에서 확인 불가.
        self._dim_verified = False
        logger.info(
            "VLLMEmbeddingProvider initialized — base_url=%s model=%s dim=%d",
            self._base_url, model_name, dim,
        )

    async def embed(self, texts: list[str]) -> EmbeddingResult:
        if not texts:
            return EmbeddingResult(vectors=[], model=self.model, dim=self.dim)
        url = f"{self._base_url}{_EMBED_PATH}"
        payload = {"model": self.model, "input": texts}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as e:
            logger.error(
                "vLLM embedding 호출 실패 (%s): %s (url=%s, n_texts=%d)",
                type(e).__name__, e, url, len(texts),
            )
            raise

        # vLLM 응답이 보통 index 순이지만 OpenAI spec 상 보장 X — 정렬로 안전성.
        items = sorted(data["data"], key=lambda x: x["index"])
        vectors = [item["embedding"] for item in items]

        if not self._dim_verified and vectors:
            actual_dim = len(vectors[0])
            if actual_dim != self.dim:
                logger.warning(
                    "EMBEDDING_DIM=%d 이지만 vLLM 응답 차원은 %d. 설정을 맞추세요.",
                    self.dim, actual_dim,
                )
                self.dim = actual_dim
            self._dim_verified = True

        return EmbeddingResult(vectors=vectors, model=self.model, dim=self.dim)
