"""
wiki list 검색 SQL 확장 (2026-05-27) — items.user_notes + alt_urls 매칭 회귀 방지.

dedup 시 새 URL 이 source_metadata.alt_urls 에 누적 + wiki list 검색이 그것도
매칭. 사용자 사례: share.google URL 과 hada.io/topic URL 이 같은 raw_content
라서 dedup 됐지만, hada URL 로도 검색돼야.
"""

from __future__ import annotations

from backend.api import wiki as wiki_api


def test_search_predicate_includes_user_notes():
    """검색 predicate 가 items.user_notes 매칭 — dedup 시 caption 으로 들어온 URL 검색."""
    assert "i.user_notes ILIKE" in wiki_api._SEARCH_PREDICATE


def test_search_predicate_includes_alt_urls():
    """검색 predicate 가 source_metadata.alt_urls 매칭 — dedup 시 신규 URL 누적."""
    assert "alt_urls" in wiki_api._SEARCH_PREDICATE


def test_search_predicate_includes_sources_title_and_url():
    """기존 매칭 (commit 3184d83) 유지 — title + source_url."""
    assert "i.title ILIKE" in wiki_api._SEARCH_PREDICATE
    assert "i.source_url ILIKE" in wiki_api._SEARCH_PREDICATE


def test_search_predicate_includes_wiki_meta():
    """wiki 자체 메타 (title/desc/slug/body/keywords)."""
    p = wiki_api._SEARCH_PREDICATE
    assert "wp.title ILIKE" in p
    assert "wp.description ILIKE" in p
    assert "wp.slug ILIKE" in p
    assert "wp.body ILIKE" in p
    assert "UNNEST(wp.keywords)" in p


def test_add_alt_url_repository_helper_signature():
    """add_alt_url_to_item helper 가 repository 에 export — 회귀 방지."""
    from backend.db.repository import add_alt_url_to_item
    import inspect

    sig = inspect.signature(add_alt_url_to_item)
    params = list(sig.parameters.keys())
    assert "session" in params
    assert "item_id" in params
    assert "new_url" in params
