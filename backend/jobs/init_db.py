"""
DB 초기화 스크립트.

- Postgres: docker-entrypoint-initdb.d에 의해 첫 부팅 시 schema.sql이 자동 적용됨.
  → 이 스크립트는 Qdrant 컬렉션만 보장.
- 임베딩 모델을 한 번 로드해서 dim을 확인한 뒤 컬렉션 생성.

사용:
    python -m scripts.init_db
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가 (scripts/ 폴더에서 실행해도 backend 모듈 import 가능)

from backend.embedding.factory import get_embedding_provider          # noqa: E402
from backend.embedding.qdrant_store import ensure_collection          # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("linkmind.init")


async def main() -> None:
    logger.info("임베딩 provider 초기화 중...")
    embedder = get_embedding_provider()
    logger.info("모델: %s, dim=%d", embedder.model, embedder.dim)

    logger.info("Qdrant 컬렉션 확인/생성 중...")
    await ensure_collection(dim=embedder.dim)
    logger.info("✅ Qdrant 준비 완료.")


if __name__ == "__main__":
    asyncio.run(main())
