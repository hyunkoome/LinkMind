"""
GPU (CUDA) smoke — embedding 이 실제로 GPU device 에서 도는지.

CI 에서는 GPU 가 없으므로 pytest.ini 의 addopts 가 'gpu' marker 를 자동 deselect.
로컬 (RTX 4090 등) 에서만 `pytest -m gpu` 또는 `bash scripts/tests/run_gpu.sh` 로
명시적 실행.

가벼운 MiniLM 모델 사용 — 무거운 bge-m3 를 매번 받는 부담 없음.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.gpu


TINY_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
TINY_DIM = 384


@pytest.fixture(scope="module")
def cuda_available():
    """torch.cuda.is_available() — false 면 skip (GPU 없는 환경)."""
    try:
        import torch
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"torch 미설치: {e}")
    if not torch.cuda.is_available():
        pytest.skip("CUDA device 없음 — GPU 테스트 skip")
    return torch


def test_torch_cuda_basic(cuda_available):
    """torch.cuda 가 device 0 을 인식하는지 + 단순 tensor 가 CUDA 에 올라가는지."""
    torch = cuda_available
    assert torch.cuda.device_count() >= 1
    x = torch.tensor([1.0, 2.0, 3.0]).to("cuda")
    assert x.device.type == "cuda"
    assert (x * 2).sum().item() == 12.0


def test_sentence_transformers_runs_on_cuda(cuda_available):
    """MiniLM 모델을 CUDA 로 로드해서 embedding 한 번 — device 검증."""
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(TINY_MODEL, device="cuda")
    vec = model.encode(["GPU embedding smoke"], normalize_embeddings=False)
    assert vec.shape == (1, TINY_DIM)
    # 모델 내부 device 확인
    # sentence-transformers 의 일관된 attr — model.device 는 torch.device 객체.
    assert str(model.device).startswith("cuda")


def test_local_provider_uses_cuda_when_available(cuda_available):
    """LocalEmbeddingProvider 가 device 인자 또는 자동 감지로 CUDA 사용하는지.

    production code 는 보통 default 가 device='cuda' if available else 'cpu'.
    이 테스트는 그 흐름을 통과해 vector 가 나오는지 확인.
    """
    import asyncio
    from backend.embedding.local import LocalEmbeddingProvider

    provider = LocalEmbeddingProvider(model_name=TINY_MODEL, dim=TINY_DIM)
    result = asyncio.run(provider.embed(["GPU local provider smoke"]))
    assert len(result.vectors) == 1
    assert len(result.vectors[0]) == TINY_DIM
