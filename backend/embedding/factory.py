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
    if backend == "vllm":
        # vllm-embed 컨테이너 — CLAUDE.md §13 D13. 모든 프로세스 공유, GPU 1번 로드.
        from backend.embedding.vllm_embed import VLLMEmbeddingProvider
        return VLLMEmbeddingProvider(
            base_url=settings.effective_vllm_embed_base_url,
            model_name=settings.embedding_model,
            dim=settings.embedding_dim,
        )
    if backend == "tei":
        # 도입 안 함 — vLLM 으로 통일 (사용자 결정 2026-05-23). features_backlog D13 참조.
        raise NotImplementedError(
            "EMBEDDING_BACKEND=tei 는 도입 안 함. vLLM 으로 통일 (D13). "
            "EMBEDDING_BACKEND=vllm 사용."
        )
    if backend == "ollama":
        raise NotImplementedError(
            "EMBEDDING_BACKEND=ollama는 Phase 2에서 구현됩니다. "
            "Ollama embedding 모델(예: nomic-embed-text) 사전 pull 필요."
        )
    raise ValueError(f"알 수 없는 EMBEDDING_BACKEND: {backend}")
