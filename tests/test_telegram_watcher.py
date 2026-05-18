"""ai_agents.base.ChannelAgent.is_ingest_successful 단위 테스트.

Phase 2.5 (2026-05-18) 에 ChannelAgent ABC 도입 — 이전엔
ai_agents/telegram_inbox_watcher.py 의 모듈 함수 `_ingest_successful` 이었으나
backend.ingest.* 결과 dict 판정 로직은 채널 간 공통이라 ABC 의 staticmethod 로 이동.

테스트 자체는 그대로 — channel watcher daemon 이 처리 성공 시 채널에서
메시지 자동 삭제 (inbox 패턴) 트리거 조건을 검증.
"""

from __future__ import annotations

from ai_agents.base import ChannelAgent


# alias — 짧게 쓰기 위해
is_ok = ChannelAgent.is_ingest_successful


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
    assert is_ok(result) is True


def test_note_saved_returns_true():
    """URL 없이 note 가 저장된 케이스 → True."""
    result = {
        "msg_id": "2",
        "urls_ingested": [],
        "note_item_id": "abc-uuid",
    }
    assert is_ok(result) is True


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
    assert is_ok(result) is False


def test_too_short_message_returns_false():
    """URL 도 없고 note 도 없음 (텍스트가 20자 미만이라 _save_text_message 가 skip)."""
    result = {
        "msg_id": "4",
        "urls_ingested": [],
        "note_item_id": None,
    }
    assert is_ok(result) is False


def test_empty_dict_returns_false():
    """비정상 input 도 안전하게 False (KeyError 안 나게)."""
    assert is_ok({}) is False
