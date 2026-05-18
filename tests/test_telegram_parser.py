"""
backend.ingest.telegram 의 export 파서 + helper 단위 테스트.

실 데이터: tests/resources/telegram_export_sample.json (합성, LinkMind-Inbox 채널
모사). 5개 메시지 — service msg 1 / URL 포함 2 / 텍스트 메모 1 / 너무 짧음 1.

DB/네트워크 없음 — 파서 결과만 검증. ingest_telegram_message 의 실제 호출은
integration / llm 마커로 분리.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.ingest.telegram import (
    TelegramMessage,
    _extract_text,
    _find_urls,
    _strip_urls_for_caption,
    _telegram_permalink,
    parse_export_messages,
)


FIXTURE = Path(__file__).parent / "resources" / "telegram_export_sample.json"


# ── helper ──────────────────────────────────────────────────


def test_find_urls_basic():
    out = _find_urls("논문: https://arxiv.org/abs/2106.09685 (LoRA)")
    assert out == ["https://arxiv.org/abs/2106.09685"]


def test_find_urls_strips_trailing_punctuation():
    out = _find_urls("see https://github.com/foo/bar.")
    assert out == ["https://github.com/foo/bar"]


def test_find_urls_dedup_and_order():
    out = _find_urls("https://a.com 보고 https://b.com 그리고 또 https://a.com")
    assert out == ["https://a.com", "https://b.com"]


def test_extract_text_string():
    assert _extract_text("hello") == "hello"


def test_extract_text_list_with_entities():
    raw = ["hello ", {"type": "link", "text": "https://x.com"}, " end."]
    assert _extract_text(raw) == "hello https://x.com end."


def test_extract_text_none():
    assert _extract_text(None) == ""
    assert _extract_text([]) == ""


def test_telegram_permalink_private_channel():
    """private supergroup id 가 -1001234567890 이면 t.me/c/1234567890/<msg>."""
    assert _telegram_permalink("-1001234567890", 42) == "https://t.me/c/1234567890/42"


def test_telegram_permalink_returns_none_for_missing():
    assert _telegram_permalink(None, 1) is None
    assert _telegram_permalink("123", None) is None


# ── caption helper (Phase 2.5 wave-3) ──────────────────────


def test_strip_urls_for_caption_keeps_user_note():
    """URL 과 같이 온 사용자 메모는 caption 으로 살아남아야 한다."""
    text = "https://arxiv.org/abs/2106.09685 LoRA 논문 — 어댑터 fine-tuning 핵심"
    assert _strip_urls_for_caption(text) == "LoRA 논문 — 어댑터 fine-tuning 핵심"


def test_strip_urls_for_caption_multiple_urls():
    text = "https://a.com 좋은 자료 https://b.com 두 번째 링크"
    out = _strip_urls_for_caption(text)
    # URL 두 개가 제거되고 사이의 메모 단어들이 단일 공백으로 연결
    assert "좋은 자료" in out
    assert "두 번째 링크" in out
    assert "https" not in out


def test_strip_urls_for_caption_url_only_returns_empty():
    """URL 만 있고 메모 없으면 빈 문자열 — note 만들 만큼의 의미 없음."""
    assert _strip_urls_for_caption("https://arxiv.org/abs/2106.09685") == ""


def test_strip_urls_for_caption_too_short_returns_empty():
    """잔여 텍스트가 5자 미만이면 caption 가치 없음 (noise)."""
    assert _strip_urls_for_caption("https://x.com 음") == ""


def test_strip_urls_for_caption_empty_input():
    assert _strip_urls_for_caption("") == ""
    assert _strip_urls_for_caption("   ") == ""


# ── parse_export_messages (실 fixture) ──────────────────────


def test_parse_export_yields_only_message_type():
    """service msg (channel_join 등) 는 skip — 'type'=='message' 만."""
    msgs = list(parse_export_messages(FIXTURE))
    # 5 msgs 중 4 (id 2~5) — id 1 은 service.
    assert len(msgs) == 4
    assert all(isinstance(m, TelegramMessage) for m in msgs)
    ids = sorted(m.msg_id for m in msgs)
    assert ids == [2, 3, 4, 5]


def test_parse_export_string_text():
    msgs = list(parse_export_messages(FIXTURE))
    msg2 = next(m for m in msgs if m.msg_id == 2)
    assert "arxiv.org/abs/2511.20343" in msg2.text
    assert msg2.sender == "hkkim"
    assert msg2.channel == "LinkMind-Inbox"
    assert msg2.channel_id == "-1001234567890"
    # permalink 도 생성됐는지
    assert msg2.permalink == "https://t.me/c/1234567890/2"


def test_parse_export_entity_text_concatenated():
    """text 가 list 형태 (entity 섞임) 인 메시지도 평문으로 합쳐져야."""
    msgs = list(parse_export_messages(FIXTURE))
    msg3 = next(m for m in msgs if m.msg_id == 3)
    assert msg3.text == "관련 코드 github.com/HengyiWang/amb3r — 같은 주제."
    # URL 추출도 동작 (https:// 가 아니라 entity href 에 있던 거라 fall-through 안 잡힘 — 의도)
    # text 안에는 'github.com/...' 만 있고 'https://' 가 없으므로 _find_urls 결과는 []
    assert _find_urls(msg3.text) == []


def test_parse_export_date_unixtime():
    msgs = list(parse_export_messages(FIXTURE))
    msg2 = next(m for m in msgs if m.msg_id == 2)
    assert msg2.date is not None
    assert msg2.date.year == 2027 or msg2.date.year == 2026 or msg2.date.year == 2028
    # 정확한 unix timestamp 1810541100 → 2027-vicinity. test 는 valid datetime 인지만.


def test_parse_export_directory_fallback(tmp_path: Path):
    """디렉토리 입력 시 result.json 우선, 없으면 첫 .json 사용."""
    # tmp_path 에 fixture 를 다른 이름으로 복사
    dest = tmp_path / "alt_name.json"
    dest.write_bytes(FIXTURE.read_bytes())
    msgs = list(parse_export_messages(tmp_path))
    assert len(msgs) == 4


def test_parse_export_missing_file_raises(tmp_path: Path):
    with pytest.raises(ValueError, match="Telegram export JSON"):
        list(parse_export_messages(tmp_path))


# ── 분류 흐름 (URL vs note) ─────────────────────────────────


def test_message_id_2_has_urls():
    """msg id 2 는 arxiv URL 1개 — ingest 흐름에서 URL 라우팅."""
    msgs = list(parse_export_messages(FIXTURE))
    msg = next(m for m in msgs if m.msg_id == 2)
    urls = _find_urls(msg.text)
    assert urls == ["https://arxiv.org/abs/2511.20343"]


def test_message_id_4_is_note():
    """msg id 4 는 URL 없는 메모 텍스트 — source_type='telegram' note 로 저장 대상."""
    msgs = list(parse_export_messages(FIXTURE))
    msg = next(m for m in msgs if m.msg_id == 4)
    assert _find_urls(msg.text) == []
    assert len(msg.text) >= 20  # note 저장 threshold


def test_message_id_5_too_short_to_save_as_note():
    """msg id 5 는 'ㅋ' 한 글자 — 저장 안 됨 (>=20 char 조건)."""
    msgs = list(parse_export_messages(FIXTURE))
    msg = next(m for m in msgs if m.msg_id == 5)
    assert len(msg.text.strip()) < 20
