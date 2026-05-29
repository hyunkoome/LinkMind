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


def test_classifier_invoke_topic_based_wiki_creation():
    """classifier invoke() 가 item 의 topics → wiki 자동 보장 (D11 D10.5 fix, 2026-05-27).

    배경: 옛 ca431aa 의 self_wiki 무조건 INSERT 가 외부 ID wiki + self_wiki 중복
    (6,625건) 의 원인. fix: item 의 topics 를 보고 external_id 있으면 그 wiki 보장 +
    self_wiki skip. external_id 없으면 fallback topic = self_wiki.

    D10.6 (2026-05-28): ext/fallback 분류 + confidence 필터는 순수 헬퍼
    _select_identity_topics_for_wiki 로 추출 (동작 검증은 test_classifier_identity_topics).
    여기선 invoke 가 topics 를 confidence 와 함께 조회 + 헬퍼 사용 + slug/role 처리
    하는지 source 패턴만 회귀 검증.
    """
    import inspect
    from backend.agents.classifier import (
        ClassifierAgent,
        _select_identity_topics_for_wiki,
    )

    src = inspect.getsource(ClassifierAgent.invoke)
    # item_topics 조회 — 사용자 mental model 의 진입점 + D10.6: confidence 동반 조회
    assert "FROM item_topics it" in src
    assert "it.confidence" in src
    # 정체성 topic 선별을 헬퍼에 위임
    assert "_select_identity_topics_for_wiki" in src
    # role='self' 는 fallback wiki link 시 사용
    assert '"role": "self"' in src or '"role": role' in src
    # sanitize_wiki_slug 사용 — backfill 과 같은 slug 패턴
    assert "sanitize_wiki_slug" in src

    # 헬퍼는 external_id vs fallback 분류 + 'url:item:' fallback 판단을 담당
    helper_src = inspect.getsource(_select_identity_topics_for_wiki)
    assert "ext_topics" in helper_src
    assert "fallback_topics" in helper_src
    assert "'url:item:'" in helper_src or '"url:item:"' in helper_src
