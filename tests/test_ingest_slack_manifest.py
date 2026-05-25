"""
tests/test_ingest_slack_manifest.py
----------------------------------------------------------------------------
D12 wave 의 manifest 재처리 job pure helper 단위 테스트.

_normalize_url / _entry_to_message — DB 호출 없는 순수 helpers.
"""
from __future__ import annotations

from datetime import timezone

from backend.jobs.ingest_slack_manifest import (
    _UNRESOLVED_ACTIONS,
    _entry_to_message,
    _normalize_url,
    _ts_to_datetime,
)


# ── _normalize_url ──────────────────────────────────────────────


def test_normalize_정상_https_그대로() -> None:
    assert _normalize_url("https://github.com/x/y") == "https://github.com/x/y"


def test_normalize_정상_http_그대로() -> None:
    assert _normalize_url("http://example.com/path") == "http://example.com/path"


def test_normalize_None_None() -> None:
    assert _normalize_url(None) is None  # type: ignore[arg-type]


def test_normalize_빈문자열_None() -> None:
    assert _normalize_url("") is None
    assert _normalize_url("   ") is None


def test_normalize_protocol_누락_https_prefix() -> None:
    """exception 케이스 'Request URL is missing http://' — 자동 보강."""
    assert _normalize_url("www.example.com/path") == "https://www.example.com/path"
    assert _normalize_url("github.com/user/repo") == "https://github.com/user/repo"


def test_normalize_프로토콜_없는_쉬레쉬_시작() -> None:
    assert _normalize_url("//cdn.example.com/img.png") == "https://cdn.example.com/img.png"


def test_normalize_명백히_쓰레기_None() -> None:
    """host 형태가 아닌 (점 없음) 토큰 → None."""
    assert _normalize_url("randomtext") is None


def test_normalize_공백_strip() -> None:
    assert _normalize_url("   https://x.com/a   ") == "https://x.com/a"


# ── _ts_to_datetime ──────────────────────────────────────────────


def test_ts_정상_변환() -> None:
    dt = _ts_to_datetime("1720622606.581589")
    assert dt is not None
    assert dt.tzinfo == timezone.utc
    assert dt.year >= 2024


def test_ts_None_또는_빈_문자열_None() -> None:
    assert _ts_to_datetime("") is None
    assert _ts_to_datetime("not-a-ts") is None


# ── _entry_to_message ───────────────────────────────────────────


def test_entry_to_message_full() -> None:
    entry = {
        "ts": "1743569850.788249",
        "channel": "2025년1분기-usa",
        "permalink": "https://w.slack.com/archives/C0/p1234",
        "url": "https://medium.com/x/y",
        "kind": "url",
        "issue": "placeholder",
        "error": None,
    }
    msg = _entry_to_message(entry)
    assert msg.ts == "1743569850.788249"
    assert msg.channel == "2025년1분기-usa"
    assert msg.permalink == "https://w.slack.com/archives/C0/p1234"
    assert msg.urls == ["https://medium.com/x/y"]
    assert msg.attachments == []
    assert msg.date is not None
    # manifest 가 가벼워서 user/channel_id/thread 는 없음
    assert msg.user is None
    assert msg.channel_id is None
    assert msg.thread_ts is None


def test_entry_to_message_url_없으면_빈_urls() -> None:
    entry = {"ts": "1234567890.123", "channel": "C0", "permalink": None, "url": None}
    msg = _entry_to_message(entry)
    assert msg.urls == []


def test_entry_to_message_minimal() -> None:
    """필수 필드만 있어도 동작."""
    msg = _entry_to_message({"ts": "1700000000.0", "channel": "ch", "url": "https://x.com"})
    assert msg.ts == "1700000000.0"
    assert msg.channel == "ch"
    assert msg.urls == ["https://x.com"]
    assert msg.permalink is None


# ── _UNRESOLVED_ACTIONS — manifest 분리 기준 ─────────────────────


def test_unresolved_actions_분류_안전() -> None:
    """unresolved manifest 에 들어갈 action 분류 — DB 에 정상 들어간 케이스 제외."""
    # 들어간 케이스 (unresolved 아님)
    assert "merged_only" not in _UNRESOLVED_ACTIONS
    assert "retried_success" not in _UNRESOLVED_ACTIONS
    # 미해결 (unresolved 맞음)
    assert "skip" in _UNRESOLVED_ACTIONS
    assert "error" in _UNRESOLVED_ACTIONS
    assert "saved_placeholder" in _UNRESOLVED_ACTIONS
    assert "retried_placeholder" in _UNRESOLVED_ACTIONS
