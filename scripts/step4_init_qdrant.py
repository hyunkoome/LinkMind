"""
Qdrant 컬렉션 사전 생성 스크립트.

`uvicorn backend.main:app` 실행 시에도 첫 ingest 호출에서 자동 생성되지만,
개발 환경에서 미리 생성해두면 디버깅이 편하다.

실행:
    python scripts/step4_init_qdrant.py

검증:
    bash scripts/step4_check_qdrant.sh
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

# 프로젝트 루트를 sys.path 에 추가 (스크립트로 실행 시 필요)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import get_settings  # noqa: E402
from backend.embedding.factory import get_embedding_provider  # noqa: E402
from backend.embedding.qdrant_store import ensure_collection  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("init_qdrant")


async def main() -> None:
    settings = get_settings()
    logger.info("EMBEDDING_BACKEND=%s MODEL=%s DIM=%d",
                settings.embedding_backend, settings.embedding_model, settings.embedding_dim)
    embedder = get_embedding_provider()
    logger.info("실제 모델 로드 완료: dim=%d", embedder.dim)
    await ensure_collection(dim=embedder.dim)
    logger.info("Qdrant 컬렉션 '%s' 준비 완료", settings.qdrant_collection)


if __name__ == "__main__":
    asyncio.run(main())
