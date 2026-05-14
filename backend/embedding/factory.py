"""Embedding provider factory."""

from __future__ import annotations

from functools import lru_cache

from backend.config import get_settings
from backend.embedding.base import EmbeddingProvider


@lru_cache(maxsize=1)
def get_embedding_provider() -> EmbeddingProvider:
    settings = get_settings()
    backend = settings.embedding_backend

    if backend == "local":
        from backend.embedding.local import LocalEmbeddingProvider
        return LocalEmbeddingProvider(
            model_name=settings.embedding_model,
            dim=settings.embedding_dim,
        )
    if backend == "tei":
        # Phase 2 — TEI HTTP 클라이언트는 별도 구현 예정.
        raise NotImplementedError(
            "EMBEDDING_BACKEND=tei는 Phase 2에서 구현됩니다. "
            "backend/embedding/tei.py 작성 + compose phase2 profile 활성화 필요."
        )
    if backend == "ollama":
        raise NotImplementedError(
            "EMBEDDING_BACKEND=ollama는 Phase 2에서 구현됩니다. "
            "Ollama embedding 모델(예: nomic-embed-text) 사전 pull 필요."
        )
    raise ValueError(f"알 수 없는 EMBEDDING_BACKEND: {backend}")
