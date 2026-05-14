"""
Embedding Provider 추상화.

backends:
  - local  : sentence-transformers (bge-m3) MVP 기본
  - tei    : HuggingFace Text Embeddings Inference (Phase 2)
  - ollama : Ollama embedding endpoint
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class EmbeddingResult:
    vectors: list[list[float]]
    model: str
    dim: int


class EmbeddingProvider(ABC):
    name: str
    model: str
    dim: int

    @abstractmethod
    async def embed(self, texts: list[str]) -> EmbeddingResult:
        raise NotImplementedError
