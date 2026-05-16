"""
ai_agents/telegram_inbox_watcher.py 의 helper 함수 단위 테스트.

watcher daemon 자체는 LinkMind backend 외부 (CLAUDE.md §3 NEVER) 라 ai_agents/ 에
있지만, 내부 결정 로직 (ingest 성공/실패 판별) 은 pure function 이라 default test
suite 에 포함 — 회귀 방지 + CI 도 검증.
"""

from __future__ import annotations

import sys
from pathlib import Path

# ai_agents/ 는 Python package 가 아니라 모듈 단독. sys.path 에 추가해서 import.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai_agents"))

from telegram_inbox_watcher import _ingest_successful  # noqa: E402


# ── 성공 케이스 ──────────────────────────────────────────────


def test_urls_all_succeeded_returns_true():
    """모든 url 이 에러 없이 처리 → True (채널에서 삭제 트리거)."""
    result = {
        "msg_id": "1",
        "urls_ingested": [
            {"url": "https://x.com", "item_id": "..."},
            {"url": "https://y.com", "item_id": "..."},
        ],
        "note_item_id": None,
    }
    assert _ingest_successful(result) is True


def test_note_saved_returns_true():
    """URL 없이 note 가 저장된 케이스 → True."""
    result = {
        "msg_id": "2",
        "urls_ingested": [],
        "note_item_id": "abc-uuid",
    }
    assert _ingest_successful(result) is True


# ── 실패 / 보존 케이스 ──────────────────────────────────────


def test_url_with_error_returns_false():
    """url 한 개라도 error 키 있으면 False — 메시지 보존 (사용자 알아챔)."""
    result = {
        "msg_id": "3",
        "urls_ingested": [
            {"url": "https://x.com", "item_id": "..."},
            {"url": "https://bad.com", "error": "ConnectionError"},
        ],
        "note_item_id": None,
    }
    assert _ingest_successful(result) is False


def test_too_short_message_returns_false():
    """URL 도 없고 note 도 없음 (텍스트가 20자 미만이라 _save_text_message 가 skip)."""
    result = {
        "msg_id": "4",
        "urls_ingested": [],
        "note_item_id": None,
    }
    assert _ingest_successful(result) is False


def test_empty_dict_returns_false():
    """비정상 input 도 안전하게 False (KeyError 안 나게)."""
    assert _ingest_successful({}) is False
