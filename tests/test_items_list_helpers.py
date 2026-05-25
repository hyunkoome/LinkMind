"""
tests/test_items_list_helpers.py
----------------------------------------------------------------------------
D12 의 backend /items list endpoint pure helper 단위 테스트.

_truncate / _extract_domain / _build_where 의 동작 검증.
DB 호출 없는 순수 함수만 — fast cpu marker.
"""
from __future__ import annotations

from backend.api.items import _build_where, _extract_domain, _truncate


def test_truncate_None_그대로_None() -> None:
    assert _truncate(None, 10) is None


def test_truncate_빈문자열_그대로() -> None:
    assert _truncate("", 10) == ""


def test_truncate_짧으면_그대로() -> None:
    assert _truncate("abc", 10) == "abc"


def test_truncate_정확히_같으면_그대로() -> None:
    assert _truncate("0123456789", 10) == "0123456789"


def test_truncate_길면_ellipsis() -> None:
    assert _truncate("0123456789abc", 10) == "0123456789…"


def test_truncate_strip_leading_whitespace() -> None:
    assert _truncate("  hello  ", 10) == "hello"


def test_extract_domain_None() -> None:
    assert _extract_domain(None) is None


def test_extract_domain_local_path_무시() -> None:
    assert _extract_domain("/files/abc123") is None


def test_extract_domain_https() -> None:
    assert _extract_domain("https://github.com/user/repo") == "github.com"


def test_extract_domain_www_제거() -> None:
    assert _extract_domain("https://www.example.com/path") == "example.com"


def test_extract_domain_subdomain_유지() -> None:
    assert _extract_domain("https://m.blog.naver.com/post/1") == "m.blog.naver.com"


def test_build_where_빈_필터() -> None:
    where, params = _build_where(
        kind=None, source_type=None, domain=None,
        has_user_notes=None, has_summary=None, q=None,
    )
    assert where == []
    assert params == {}


def test_build_where_kind_filter() -> None:
    where, params = _build_where(
        kind="image_no_ocr", source_type=None, domain=None,
        has_user_notes=None, has_summary=None, q=None,
    )
    assert len(where) == 1
    assert "fetch_error_kind" in where[0]
    assert params == {"kind": "image_no_ocr"}


def test_build_where_kind_none_정상자료() -> None:
    """kind='none' 은 cleanup 필요 없는 정상 자료 — fetch_error_kind 없음."""
    where, params = _build_where(
        kind="none", source_type=None, domain=None,
        has_user_notes=None, has_summary=None, q=None,
    )
    assert len(where) == 1
    assert "NOT" in where[0] and "fetch_error_kind" in where[0]
    assert "kind" not in params


def test_build_where_여러_필터_조합() -> None:
    where, params = _build_where(
        kind="extraction_failed", source_type="url", domain="LINKEDIN.COM",
        has_user_notes=True, has_summary=False, q="머신러닝",
    )
    assert len(where) == 6
    assert params["kind"] == "extraction_failed"
    assert params["source_type"] == "url"
    assert params["domain"] == "linkedin.com"   # lower-cased
    assert "%머신러닝%" == params["q"]


def test_build_where_has_user_notes_True() -> None:
    where, params = _build_where(
        kind=None, source_type=None, domain=None,
        has_user_notes=True, has_summary=None, q=None,
    )
    assert len(where) == 1
    assert "user_notes IS NOT NULL" in where[0]
    assert params == {}


def test_build_where_has_user_notes_False() -> None:
    where, params = _build_where(
        kind=None, source_type=None, domain=None,
        has_user_notes=False, has_summary=None, q=None,
    )
    assert len(where) == 1
    assert "IS NULL" in where[0]


def test_build_where_skip_kind() -> None:
    """facet 쿼리용 — kind 필터 제외."""
    where, params = _build_where(
        kind="image_no_ocr", source_type="document", domain=None,
        has_user_notes=None, has_summary=None, q=None,
        skip_kind=True,
    )
    # kind 절은 빠짐, source_type 절은 살아있음
    assert len(where) == 1
    assert "source_type" in where[0]
    assert "kind" not in params


def test_build_where_skip_source_type() -> None:
    where, params = _build_where(
        kind="image_no_ocr", source_type="document", domain=None,
        has_user_notes=None, has_summary=None, q=None,
        skip_source_type=True,
    )
    assert len(where) == 1
    assert "fetch_error_kind" in where[0]
    assert "source_type" not in params


def test_build_where_q_빈문자열_무시() -> None:
    where, params = _build_where(
        kind=None, source_type=None, domain=None,
        has_user_notes=None, has_summary=None, q="   ",
    )
    assert where == []
    assert params == {}


def test_build_where_q_strip_적용() -> None:
    where, params = _build_where(
        kind=None, source_type=None, domain=None,
        has_user_notes=None, has_summary=None, q="  search 단어  ",
    )
    assert len(where) == 1
    assert params == {"q": "%search 단어%"}
