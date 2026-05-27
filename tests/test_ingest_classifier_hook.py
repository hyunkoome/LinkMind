"""
backend/api/ingest.py 의 classifier BackgroundTask hook (2026-05-27) cpu unit test.

_wrap_result 가 BackgroundTasks 받았을 때 적절히 schedule 하는지만 검증. 실제
classifier 호출은 별 session + LLM 이라 integration test (backend live) 영역.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backend.api.ingest import _wrap_result


def _mock_bg() -> MagicMock:
    """FastAPI BackgroundTasks 의 add_task 호출만 추적하는 mock."""
    bg = MagicMock()
    bg.add_task = MagicMock()
    return bg


def test_wrap_result_no_background_no_schedule():
    """background=None 이면 task schedule 안 함 (단순 응답)."""
    result = {
        "item_id": "abc-123",
        "created": True,
        "summary_generated": True,
        "title": "test",
    }
    resp = _wrap_result(result, background=None)
    assert resp.item_id == "abc-123"
    assert resp.summary_generated is True


def test_wrap_result_schedules_when_summary_generated():
    """summary_generated=True + background 주어지면 classifier task schedule."""
    bg = _mock_bg()
    result = {
        "item_id": "abc-123",
        "created": True,
        "summary_generated": True,
        "title": "test",
    }
    _wrap_result(result, background=bg)
    assert bg.add_task.call_count == 1
    # 첫 인자 = _bg_classify_to_wiki, 두 번째 = item_id str
    args = bg.add_task.call_args
    assert args.args[1] == "abc-123"


def test_wrap_result_no_schedule_when_no_summary():
    """summary_generated=False (또는 missing) 이면 schedule 안 함."""
    bg = _mock_bg()
    result = {
        "item_id": "abc-123",
        "created": True,
        "summary_generated": False,
    }
    _wrap_result(result, background=bg)
    assert bg.add_task.call_count == 0


def test_wrap_result_schedules_for_refreshed_item():
    """force 재분석 (created=False, refreshed=True) 도 schedule.

    이유: summary 가 새로 갱신됐다면 wiki 매핑도 재검토 의미. classifier 의
    ON CONFLICT UPDATE 로 옛 매핑 보존 + confidence 만 갱신.
    """
    bg = _mock_bg()
    result = {
        "item_id": "abc-456",
        "created": False,
        "refreshed": True,
        "summary_generated": True,
    }
    _wrap_result(result, background=bg)
    assert bg.add_task.call_count == 1


def test_wrap_result_response_fields_preserved():
    """schedule 여부와 무관하게 응답 객체 필드는 그대로."""
    bg = _mock_bg()
    result = {
        "item_id": "abc-789",
        "created": True,
        "summary_generated": True,
        "chunks_indexed": 42,
        "figures_saved": 3,
        "tags": ["a", "b"],
        "title": "T",
    }
    resp = _wrap_result(result, background=bg)
    assert resp.chunks_indexed == 42
    assert resp.figures_saved == 3
    assert resp.tags == ["a", "b"]
    assert resp.title == "T"


def test_ingest_router_endpoints_take_background_tasks():
    """6 ingest endpoint 가 BackgroundTasks 인자를 받는지 시그니처 검증.

    회귀 방지 — endpoint 시그니처에서 BackgroundTasks 가 빠지면 classifier hook
    이 silent 작동 안 함 (build_context 가 이걸 받지 않아도 200 응답).

    PEP 563 (`from __future__ import annotations`) 로 annotation 이 string 으로 저장
    되므로 문자열 비교 (eval 까지 가지 않고 가벼움).
    """
    import inspect

    from backend.api.ingest import (
        ingest_auto,
        ingest_github_endpoint,
        ingest_pdf_endpoint,
        ingest_pdf_upload,
        ingest_url_endpoint,
        ingest_youtube_endpoint,
    )

    endpoints = {
        "ingest_url_endpoint": ingest_url_endpoint,
        "ingest_youtube_endpoint": ingest_youtube_endpoint,
        "ingest_github_endpoint": ingest_github_endpoint,
        "ingest_pdf_endpoint": ingest_pdf_endpoint,
        "ingest_pdf_upload": ingest_pdf_upload,
        "ingest_auto": ingest_auto,
    }
    missing = []
    for name, fn in endpoints.items():
        sig = inspect.signature(fn)
        annotations = {str(p.annotation) for p in sig.parameters.values()}
        if "BackgroundTasks" not in annotations:
            missing.append(name)
    assert not missing, f"BackgroundTasks 인자 누락 endpoint: {missing}"
