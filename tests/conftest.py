"""pytest 공통 설정.

대부분의 테스트는 DB/Qdrant/LLM 접속이 필요 없는 순수 함수 단위라서 fixtures 가
거의 없다. backend.* import 시 settings 가 한 번 로드되는데, env/dev.env 가 없는
환경 (CI 등) 에서도 import 만은 되도록 최소한의 env 만 설정.
"""

from __future__ import annotations

import os

# 테스트 import 단계에서 settings 가 필수 키 검증 못 통과해서 깨지는 일이 없도록
# 더미값 주입. 실제 DB/Qdrant 호출하는 테스트는 별도 marker 로 분리해야 하지만
# 현재 추가된 테스트는 모두 순수 함수.
os.environ.setdefault("POSTGRES_USER", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")
os.environ.setdefault("POSTGRES_DB", "test")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("POSTGRES_PORT", "5432")
os.environ.setdefault("QDRANT_URL", "http://localhost:6333")
os.environ.setdefault("OLLAMA_BASE_URL", "http://localhost:11434")
