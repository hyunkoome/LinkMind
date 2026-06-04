"""
하이브리드 RAG 위키 검색 회귀 방지 (2026-06-04 버그).

버그: writer 가 Qdrant payload 에 body_status='ready' 로 넣는데 Postgres 와 ask.py 의
status_filter 는 'completed' → 의미검색이 항상 0건 → 하이브리드 RAG 에서 위키 본문이
통째로 빠졌다. 세 곳이 같은 상수(WIKI_STATUS_COMPLETED)를 쓰는지 검증.

순수 소스 검사 — DB/Qdrant/네트워크 불필요 (cpu 카테고리).
"""

from __future__ import annotations

import inspect

from backend.agents import writer as writer_module
from backend.api import ask as ask_module
from backend.embedding.wiki_qdrant import WIKI_STATUS_COMPLETED


def test_status_constant_value():
    # Postgres _UPDATE_BODY_SQL 이 'completed' 로 저장하므로 상수도 'completed'.
    assert WIKI_STATUS_COMPLETED == "completed"


def test_postgres_update_sql_uses_completed():
    # writer 의 UPDATE 문이 상수와 같은 값을 써야 Qdrant payload 와 일치.
    assert "body_status = 'completed'" in str(writer_module._UPDATE_BODY_SQL)


def test_writer_qdrant_payload_uses_shared_constant():
    # writer 가 Qdrant payload 에 'ready' 같은 리터럴이 아니라 공유 상수를 넣어야 한다.
    src = inspect.getsource(writer_module)
    assert '"body_status": WIKI_STATUS_COMPLETED' in src
    assert '"body_status": "ready"' not in src


def test_ask_retrieve_wikis_filters_by_shared_constant():
    # ask.py 의 위키 검색 필터도 같은 상수를 써야 한다.
    src = inspect.getsource(ask_module._retrieve_wikis)
    assert "status_filter=[WIKI_STATUS_COMPLETED]" in src
