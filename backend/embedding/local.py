"""
sentence-transformers 기반 로컬 임베딩 (MVP 기본).

bge-m3 등 HuggingFace 모델을 GPU에 로드. 첫 호출 시 모델 다운로드(약 1.4GB).
"""

from __future__ import annotations

import asyncio
import logging

from backend.embedding.base import EmbeddingProvider, EmbeddingResult

logger = logging.getLogger(__name__)


class LocalEmbeddingProvider(EmbeddingProvider):
    name = "local"

    def __init__(self, model_name: str, dim: int) -> None:
        # heavy import는 클래스 사용 시점에만.
        from sentence_transformers import SentenceTransformer
        import torch

        self.model = model_name
        self.dim = dim
        device = "cuda" if torch.cuda.is_available() else "cpu"
        logger.info("임베딩 모델 로드 시작: %s (device=%s)", model_name, device)
        self._model = SentenceTransformer(model_name, device=device)
        # 차원 검증 — 설정과 실제 모델이 다르면 즉시 알 수 있도록.
        actual_dim = self._model.get_sentence_embedding_dimension()
        if actual_dim != dim:
            logger.warning(
                "EMBEDDING_DIM=%d 이지만 모델 실제 차원은 %d. 설정을 맞추세요.",
                dim, actual_dim,
            )
            self.dim = actual_dim or dim

    async def embed(self, texts: list[str]) -> EmbeddingResult:
        if not texts:
            return EmbeddingResult(vectors=[], model=self.model, dim=self.dim)
        # sentence-transformers는 동기 호출. CPU/GPU 작업을 이벤트 루프 차단 안 하도록 to_thread.
        vectors = await asyncio.to_thread(
            lambda: self._model.encode(
                texts,
                normalize_embeddings=True,
                show_progress_bar=False,
                convert_to_numpy=True,
            ).tolist()
        )
        return EmbeddingResult(vectors=vectors, model=self.model, dim=self.dim)
