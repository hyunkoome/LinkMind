"""backend/api/items.py + backend/llm/keyword_extract.py 의 pure 함수 / Pydantic 단위 테스트.

API endpoint 자체 (e2e) 는 tests/integration/ 의 backend live 가 필요 — 여기는
DB 없이 pure 함수 / pydantic 검증만.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.api.items import _merge_keep_order, _TAG_MAX
from backend.llm.keyword_extract import (
    _MIN_NOTES_LENGTH,
    _normalize_keyword,
    _parse_keywords,
)
from backend.schemas.models import ItemUpdateRequest


# ── _merge_keep_order ────────────────────────────────────────


def test_merge_keep_order_existing_first():
    """기존 tags 가 먼저, 신규 키워드는 append."""
    assert _merge_keep_order(["A", "B"], ["C", "D"]) == ["A", "B", "C", "D"]


def test_merge_keep_order_dedup_case_insensitive():
    """대소문자 다른 같은 키워드 → 첫 출현 유지."""
    assert _merge_keep_order(["SLAM"], ["slam", "LiDAR"]) == ["SLAM", "LiDAR"]


def test_merge_keep_order_dedup_within_existing():
    """기존에 중복 있으면 그대로 (입력 신뢰), 신규는 dedup."""
    assert _merge_keep_order(["A", "A"], ["A", "B"]) == ["A", "B"]


def test_merge_keep_order_truncates_at_max():
    """최대 _TAG_MAX 개로 cap."""
    existing = [f"tag{i}" for i in range(_TAG_MAX - 2)]
    new = [f"new{i}" for i in range(10)]
    merged = _merge_keep_order(existing, new, max_n=_TAG_MAX)
    assert len(merged) == _TAG_MAX
    # 기존 우선 → 신규 중 2개만 들어감
    assert merged[: _TAG_MAX - 2] == existing
    assert merged[_TAG_MAX - 2:] == ["new0", "new1"]


def test_merge_keep_order_empty_inputs():
    assert _merge_keep_order([], []) == []
    assert _merge_keep_order(["A"], []) == ["A"]
    assert _merge_keep_order([], ["A"]) == ["A"]


def test_merge_keep_order_filters_empty_strings():
    assert _merge_keep_order(["A", ""], ["", "B"]) == ["A", "B"]


# ── keyword_extract pure helpers ─────────────────────────────


def test_normalize_keyword_strips_list_markers():
    """LLM 응답의 '1. 키워드', '- 키워드', '* 키워드' 형태 정리."""
    assert _normalize_keyword("1. 포인트클라우드") == "포인트클라우드"
    assert _normalize_keyword("- 3D Gaussian") == "3D Gaussian"
    assert _normalize_keyword("* SLAM") == "SLAM"
    assert _normalize_keyword("  · LiDAR  ") == "· LiDAR"  # bullet 자체는 keep (LLM 케이스 무한)


def test_normalize_keyword_strips_quotes():
    assert _normalize_keyword('"포인트클라우드"') == "포인트클라우드"
    assert _normalize_keyword("'그리네타'") == "그리네타"
    assert _normalize_keyword("“한국어 따옴표”") == "한국어 따옴표"


def test_normalize_keyword_handles_empty():
    assert _normalize_keyword("") == ""
    assert _normalize_keyword("   ") == ""


def test_parse_keywords_comma_separated():
    """LLM 응답이 '포인트클라우드, 그리네타, 3D Gaussian' 형태."""
    raw = "포인트클라우드, 그리네타, 3D Gaussian, SLAM"
    assert _parse_keywords(raw) == ["포인트클라우드", "그리네타", "3D Gaussian", "SLAM"]


def test_parse_keywords_newline_separated():
    """LLM 이 list 모양으로 답할 때 (모델 잘못 따름)."""
    raw = "1. 포인트클라우드\n2. 그리네타\n3. SLAM"
    assert _parse_keywords(raw) == ["포인트클라우드", "그리네타", "SLAM"]


def test_parse_keywords_dedup_case_insensitive():
    """SLAM / slam → 하나만."""
    raw = "SLAM, slam, LiDAR"
    assert _parse_keywords(raw) == ["SLAM", "LiDAR"]


def test_parse_keywords_caps_at_10():
    raw = ", ".join(f"k{i}" for i in range(20))
    assert len(_parse_keywords(raw)) == 10


def test_parse_keywords_drops_too_long():
    """30자 넘는 키워드는 LLM 잡설 — 제외."""
    long = "x" * 31
    raw = f"포인트클라우드, {long}, 그리네타"
    assert _parse_keywords(raw) == ["포인트클라우드", "그리네타"]


def test_parse_keywords_empty():
    assert _parse_keywords("") == []
    assert _parse_keywords("   ") == []


def test_min_notes_length_is_sensible():
    """너무 짧으면 LLM 호출 무의미 — 5~15자 정도가 적절."""
    assert 5 <= _MIN_NOTES_LENGTH <= 20


# ── ItemUpdateRequest pydantic ────────────────────────────────


def test_update_request_all_optional():
    """빈 body 도 valid — no-op patch."""
    req = ItemUpdateRequest()
    assert req.user_notes is None
    assert req.is_read is None


def test_update_request_partial_user_notes_only():
    req = ItemUpdateRequest(user_notes="hello")
    assert req.user_notes == "hello"
    assert req.is_read is None


def test_update_request_partial_is_read_only():
    req = ItemUpdateRequest(is_read=True)
    assert req.user_notes is None
    assert req.is_read is True


def test_update_request_both_fields():
    req = ItemUpdateRequest(
        user_notes="포인트클라우드 압축시 활용",
        is_read=True,
    )
    assert req.user_notes == "포인트클라우드 압축시 활용"
    assert req.is_read is True


def test_update_request_empty_string_means_clear_notes():
    """user_notes='' 는 valid — repository 에서 NULL 로 정규화."""
    req = ItemUpdateRequest(user_notes="")
    assert req.user_notes == ""


def test_update_request_rejects_invalid_is_read_type():
    """is_read 는 bool 만."""
    with pytest.raises(ValidationError):
        ItemUpdateRequest(is_read="not-a-bool")  # type: ignore[arg-type]


# ── TAG_MAX 상수 sanity ──────────────────────────────────────


def test_tag_max_is_sensible():
    assert 16 <= _TAG_MAX <= 50
