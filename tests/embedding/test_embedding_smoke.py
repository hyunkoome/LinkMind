"""
embedding 흐름 smoke — sentence-transformers + chunking + LocalEmbeddingProvider.

가벼운 MiniLM-L6-v2 모델 (~80MB, 384d) 사용 — CPU 에서 ~수초 안에 완료. CI 의 별
job 에서 도는 게 목적이라 device='cpu' 강제. GPU 가 있어도 이 테스트는 CPU 만 검증
(GPU 강제 smoke 는 tests/gpu/test_embedding_smoke_gpu.py 별도).

production 의 bge-m3 (1.4GB, 1024d) 와는 dim 이 다르므로 Qdrant 컬렉션과는 호환
되지 않음 — 이 테스트는 '코드가 모델을 로드하고 embed() 가 깨지지 않는다' 만 검증.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.embedding


TINY_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
TINY_DIM = 384


@pytest.fixture(scope="module")
def st_dependencies():
    """sentence-transformers/torch 가 import 가능한지 확인. 미설치면 skip."""
    try:
        import sentence_transformers  # noqa: F401
        import torch  # noqa: F401
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"sentence-transformers / torch 미설치: {e}")


def test_sentence_transformers_loads_tiny_model_cpu(st_dependencies):
    """MiniLM 모델을 CPU 로 로드 + 한 줄 embed."""
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(TINY_MODEL, device="cpu")
    vec = model.encode(["LinkMind embedding smoke test"], normalize_embeddings=False)
    assert vec.shape == (1, TINY_DIM)


def test_local_embedding_provider_smoke(st_dependencies, monkeypatch):
    """backend.embedding.local.LocalEmbeddingProvider 가 깨지지 않고 동작하는지.

    bge-m3 대신 MiniLM 로드해서 dim/embed 결과 검증. ensure_collection / Qdrant
    호출은 안 함 (이 단계는 embedding 자체 흐름만).
    """
    import asyncio
    from backend.embedding.local import LocalEmbeddingProvider

    # production 의 dim 검증을 우회 — 직접 model_name + dim 전달.
    provider = LocalEmbeddingProvider(model_name=TINY_MODEL, dim=TINY_DIM)
    # device 가 'cpu' 인지 — production 코드는 cuda 자동 사용, CI 에서는 CPU.
    # device 설정은 LocalEmbeddingProvider 내부에서 — 실제 결과만 검증.

    async def go():
        result = await provider.embed([
            "First chunk of text for smoke test.",
            "Second chunk — also a sample.",
        ])
        return result

    result = asyncio.run(go())
    assert len(result.vectors) == 2
    assert all(len(v) == TINY_DIM for v in result.vectors)
    # 같은 입력은 같은 vector — deterministic
    again = asyncio.run(provider.embed(["First chunk of text for smoke test."]))
    # 부동소수 비교 — 한 자리 이내 동일성 (round-trip 안정)
    assert abs(again.vectors[0][0] - result.vectors[0][0]) < 1e-4


def test_chunking_plus_embedding_pipeline(st_dependencies):
    """utils.chunking → embedding 의 pipeline 이 dim 일관성 보장."""
    import asyncio
    from backend.embedding.local import LocalEmbeddingProvider
    from backend.utils.chunking import chunk_text

    long_text = " ".join([f"Sentence number {i} of synthetic test text." for i in range(50)])
    chunks = chunk_text(long_text)
    assert len(chunks) >= 1

    provider = LocalEmbeddingProvider(model_name=TINY_MODEL, dim=TINY_DIM)
    result = asyncio.run(provider.embed(chunks))
    assert len(result.vectors) == len(chunks)
    assert all(len(v) == TINY_DIM for v in result.vectors)
