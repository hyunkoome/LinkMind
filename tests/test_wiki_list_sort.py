"""
wiki list 정렬(sort) allowlist + SQL 조립 단위 테스트 (2026-05-29).

GET /wiki 의 ?sort= 옵션 — 날짜순(최신/오래된) / 가나다(오름/내림). SQL injection
방지를 위해 allowlist key 로만 ORDER BY clause 를 lookup 한다. _build_list_sql 은
pure (sort key → SQLAlchemy text). DB 없이 SQL 문자열만 검증 (§9 결정 흐름 1).
"""

from __future__ import annotations

from backend.api.wiki import DEFAULT_WIKI_SORT, _SORT_CLAUSES, _build_list_sql


def test_sort_allowlist_keys():
    assert set(_SORT_CLAUSES) == {"recent", "oldest", "alpha", "alpha_desc"}
    assert DEFAULT_WIKI_SORT in _SORT_CLAUSES


def test_recent_is_body_generated_at_desc():
    sql = str(_build_list_sql("recent"))
    assert "body_generated_at" in sql
    assert "DESC NULLS LAST" in sql


def test_oldest_is_body_generated_at_asc():
    sql = str(_build_list_sql("oldest"))
    assert "body_generated_at" in sql
    assert "ASC NULLS LAST" in sql


def test_alpha_is_title_asc():
    assert "LOWER(wp.title) ASC" in str(_build_list_sql("alpha"))


def test_alpha_desc_is_title_desc():
    assert "LOWER(wp.title) DESC" in str(_build_list_sql("alpha_desc"))


def test_unknown_sort_falls_back_to_default():
    """allowlist 밖 값 (오타/주입 시도) → default sort 로 안전 fallback."""
    assert str(_build_list_sql("'; DROP TABLE wiki_pages; --")) == str(
        _build_list_sql(DEFAULT_WIKI_SORT)
    )


def test_keywords_in_list_select():
    """list SELECT 가 keywords 를 반환 — frontend pill 표시용."""
    assert "wp.keywords" in str(_build_list_sql("recent"))


def test_multi_keyword_is_array_contains_and():
    """다중 키워드 필터는 @> (array contains = 선택 키워드 모두 포함, AND 의미).

    :keywords NULL (선택 없음) 이면 필터 없이 통과. COALESCE 로 keywords NULL 컬럼도
    빈 배열 취급 (안전).
    """
    sql = str(_build_list_sql("recent"))
    assert "@>" in sql
    assert "CAST(:keywords AS text[])" in sql
    # asyncpg 타입 추론 위해 NULL 체크도 CAST (AmbiguousParameterError 방지)
    assert "CAST(:keywords AS text[]) IS NULL" in sql
    assert "COALESCE(wp.keywords" in sql


def test_group_order_preserved():
    """status 그룹 (completed→pending→issues) + pinned 우선 정렬 유지."""
    sql = str(_build_list_sql("alpha"))
    assert "wp.is_pinned DESC" in sql
    assert "body_processing_started_at IS NOT NULL" in sql  # pending generating 우선
