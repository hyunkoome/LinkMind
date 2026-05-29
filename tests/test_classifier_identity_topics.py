"""
ClassifierAgent 의 _select_identity_topics_for_wiki 순수 단위 테스트 (D10.6).

2026-05-28 중복 wiki fix — auto_link_topics 가 자료의 primary external_id 를
confidence=1.0 으로, cross-modal 단서 (예: YouTube 설명란의 github 링크) 를 0.7 로
단다. 옛 classifier 는 confidence 를 안 읽어서 0.7 단서까지 primary wiki 로 승격 →
같은 자료 1개가 여러 wiki 로 쪼개졌다. 이 테스트는 confidence>=임계값인 자기
정체성 topic 만 wiki 로 승격되는지 (회귀 방지) 검증한다.

DB/네트워크 없는 pure 함수 → tests/ 직접 (cpu 마커 없음, §9 결정 흐름 1).
"""

from __future__ import annotations

from backend.agents.classifier import (
    ClassifierAgent,
    _select_identity_topics_for_wiki,
)

MIN_CONF = ClassifierAgent.IDENTITY_TOPIC_MIN_CONFIDENCE


def _topic(slug: str, confidence: float, *, title: str | None = None, role: str = "video"):
    """item_topics JOIN topics 한 row 흉내 (dict — RowMapping 과 동일 indexing)."""
    return {"id": slug, "slug": slug, "title": title or slug, "confidence": confidence, "role": role}


def test_threshold_default_is_0_9():
    """0.7 (단서) < 0.9 <= 1.0 (정체성) 사이 — 둘을 명확히 가르는 값."""
    assert 0.7 < MIN_CONF <= 1.0


def test_youtube_with_github_clue_only_promotes_youtube():
    """★ 회귀 ★ YouTube `Byo7yew9-OQ` (yt primary 1.0) + 설명란 github 단서 (0.7).

    옛 버그: github 단서까지 primary wiki 로 승격 → 같은 영상이 yt__/github__ 2 wiki.
    이제 yt 만 wiki 로, github 0.7 단서는 제외 (관계로만).
    """
    topics = [
        _topic("yt:Byo7yew9-OQ", 1.0, title="생성 모델 공부의 핵심"),
        _topic("github:codingvillainkor/manim-kor", 0.7),
    ]
    to_wiki, skipped = _select_identity_topics_for_wiki(topics, MIN_CONF)
    assert [t["slug"] for t in to_wiki] == ["yt:Byo7yew9-OQ"]
    assert [t["slug"] for t in skipped] == ["github:codingvillainkor/manim-kor"]


def test_plain_url_with_youtube_clue_only_promotes_self_wiki():
    """★ 회귀 ★ 드론 영상 케이스 — url:item: fallback (1.0) + yt 단서 (0.7).

    URL 페이지가 youtube 링크를 품은 경우. self_wiki (url:item:) 만 승격하고
    yt__ 단서 wiki 는 안 만든다.
    """
    topics = [
        _topic("url:item:e5dcd93c-0000-0000-0000-000000000000", 1.0),
        _topic("yt:gO_3rWDNtRU", 0.7),
    ]
    to_wiki, skipped = _select_identity_topics_for_wiki(topics, MIN_CONF)
    assert [t["slug"] for t in to_wiki] == ["url:item:e5dcd93c-0000-0000-0000-000000000000"]
    assert [t["slug"] for t in skipped] == ["yt:gO_3rWDNtRU"]


def test_external_primary_only():
    """external_id primary 하나만 (단서 없음) → 그것만 승격."""
    topics = [_topic("arxiv:2106.09685", 1.0, title="LoRA")]
    to_wiki, skipped = _select_identity_topics_for_wiki(topics, MIN_CONF)
    assert [t["slug"] for t in to_wiki] == ["arxiv:2106.09685"]
    assert skipped == []


def test_fallback_only():
    """external_id 없는 일반 URL — fallback topic (1.0) 만 → self_wiki 승격."""
    topics = [_topic("url:item:abc", 1.0, title="어떤 블로그")]
    to_wiki, skipped = _select_identity_topics_for_wiki(topics, MIN_CONF)
    assert [t["slug"] for t in to_wiki] == ["url:item:abc"]
    assert skipped == []


def test_external_present_means_fallback_skipped():
    """external_id 정체성이 있으면 fallback (self_wiki) 은 skip (사용자 mental model).

    둘 다 1.0 이어도 (예: 수동 link) external 우선, url:item: 제외.
    """
    topics = [
        _topic("github:owner/repo", 1.0, title="repo"),
        _topic("url:item:abc", 1.0),
    ]
    to_wiki, _ = _select_identity_topics_for_wiki(topics, MIN_CONF)
    assert [t["slug"] for t in to_wiki] == ["github:owner/repo"]


def test_multiple_high_confidence_external_all_promoted():
    """여러 external 정체성이 모두 1.0 (예: 자동 primary + 수동 link) → 다 승격.

    사용자가 의도적으로 link 한 케이스라 둘 다 wiki 자격. (auto 단독으로는
    primary external 이 1개뿐이라 이 상황은 수동 link 등 deliberate 케이스.)
    """
    topics = [
        _topic("arxiv:2106.09685", 1.0, title="LoRA paper"),
        _topic("github:microsoft/LoRA", 1.0, title="LoRA code", role="code"),
    ]
    to_wiki, skipped = _select_identity_topics_for_wiki(topics, MIN_CONF)
    assert {t["slug"] for t in to_wiki} == {"arxiv:2106.09685", "github:microsoft/LoRA"}
    assert skipped == []


def test_only_low_confidence_clues_promotes_nothing():
    """정체성 topic 없이 0.7 단서만 (비정상 edge) → wiki 승격 0, 전부 skipped."""
    topics = [
        _topic("github:a/b", 0.7),
        _topic("arxiv:1234.5678", 0.7),
    ]
    to_wiki, skipped = _select_identity_topics_for_wiki(topics, MIN_CONF)
    assert to_wiki == []
    assert {t["slug"] for t in skipped} == {"github:a/b", "arxiv:1234.5678"}


def test_none_confidence_treated_as_low():
    """confidence NULL (방어적) → 0 으로 취급해 승격 제외.

    실제 스키마는 NOT NULL DEFAULT 1.0 이지만 float(None or 0) 방어 검증.
    """
    topics = [_topic("yt:abc", None)]  # type: ignore[arg-type]
    to_wiki, skipped = _select_identity_topics_for_wiki(topics, MIN_CONF)
    assert to_wiki == []
    assert [t["slug"] for t in skipped] == ["yt:abc"]


def test_empty_topics():
    """topic 없는 item → 빈 결과 (LLM matched/new_pages 흐름이 별도 처리)."""
    to_wiki, skipped = _select_identity_topics_for_wiki([], MIN_CONF)
    assert to_wiki == []
    assert skipped == []
