"""merge_duplicate_wikis pure helper 테스트 — cpu marker.

DB / Qdrant / vLLM 호출 없음 — _pick_representative, _is_external_id_wiki 등
순수 함수만. 실 실행 검증은 사용자가 --dry-run 후 --limit 1 로 별 step.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from backend.jobs.merge_duplicate_wikis import (
    _PLACEHOLDER_TITLES,
    _is_external_id_wiki,
    _is_self_wiki,
    _pick_representative,
)


def _wiki(
    slug: str,
    *,
    title: str = "Test Title",
    body: str | None = None,
    body_status: str = "issues",
    created_at: str = "2026-05-26T01:22:23+00:00",
) -> dict[str, Any]:
    return {
        "slug": slug,
        "title": title,
        "body": body,
        "body_status": body_status,
        "created_at": datetime.fromisoformat(created_at).astimezone(timezone.utc),
        "keywords": [],
        "id": f"uuid-{slug}",
        "latest_version": 0,
        "description": None,
        "updated_at": datetime.now(timezone.utc),
        "source_count": 1,
        "body_model": None,
        "body_prompt_version": None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# slug 분류
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("slug,expected", [
    ("yt__byo7yew9-oq", True),
    ("github__codingvillainkor-manim-kor", True),
    ("arxiv__2501.04227", True),
    ("doi__10.1145-3687953", True),
    ("ytpl__pl12w7vywefuwnjvrxzkzrrunipnogxhoh", True),
    ("url__item__8eed95b6-edd4-48e9-8293-b546e5c3ec1e", False),
    ("custom-slug", False),
    ("", False),
])
def test_is_external_id_wiki(slug: str, expected: bool) -> None:
    assert _is_external_id_wiki(slug) is expected


@pytest.mark.parametrize("slug,expected", [
    ("url__item__8eed95b6-edd4-48e9-8293-b546e5c3ec1e", True),
    ("url__item__00000000-0000-0000-0000-000000000000", True),
    ("yt__byo7yew9-oq", False),
    ("github__owner-repo", False),
    ("custom-slug", False),
])
def test_is_self_wiki(slug: str, expected: bool) -> None:
    assert _is_self_wiki(slug) is expected


# ─────────────────────────────────────────────────────────────────────────────
# 대표 wiki 선택 — 우선순위 검증
# ─────────────────────────────────────────────────────────────────────────────


def test_pick_representative_external_over_self() -> None:
    """external_id wiki 가 self_wiki 보다 우선 (사용자 mental model 의 핵심)."""
    self_wiki = _wiki(
        "url__item__abc-123", body="newer", body_status="completed",
        created_at="2026-05-27T03:00:00+00:00",
    )
    ext_wiki = _wiki(
        "yt__abc123", body="older", body_status="completed",
        created_at="2026-05-26T01:00:00+00:00",
    )
    target = _pick_representative([self_wiki, ext_wiki])
    assert target["slug"] == "yt__abc123"


def test_pick_representative_completed_over_pending() -> None:
    """같은 카테고리 안에서 'completed' 가 'pending' 보다 우선."""
    pending = _wiki("yt__a", body=None, body_status="pending",
                    created_at="2026-05-26T01:00:00+00:00")
    completed = _wiki("yt__b", body="body", body_status="completed",
                      created_at="2026-05-27T03:00:00+00:00")
    target = _pick_representative([pending, completed])
    assert target["slug"] == "yt__b"


def test_pick_representative_longer_body() -> None:
    """body 길이 큰 쪽 우선 (정보량 많음)."""
    short = _wiki("yt__short", body="short body",
                  body_status="completed", created_at="2026-05-26T01:00:00+00:00")
    long_body = "x" * 1000
    long_w = _wiki("yt__long", body=long_body,
                   body_status="completed", created_at="2026-05-27T03:00:00+00:00")
    target = _pick_representative([short, long_w])
    assert target["slug"] == "yt__long"


def test_pick_representative_oldest_when_tied() -> None:
    """모두 같은 조건이면 가장 오래된 것 우선 (안정성)."""
    older = _wiki("yt__older", body="same body", body_status="completed",
                  created_at="2026-05-26T01:00:00+00:00")
    newer = _wiki("yt__newer", body="same body", body_status="completed",
                  created_at="2026-05-27T03:00:00+00:00")
    target = _pick_representative([older, newer])
    assert target["slug"] == "yt__older"


def test_pick_representative_all_self_wiki() -> None:
    """모두 self_wiki 인 경우 — body 길이 / created_at 으로 분리."""
    a = _wiki("url__item__aaa", body="", body_status="issues",
              created_at="2026-05-27T03:00:00+00:00")
    b = _wiki("url__item__bbb", body="real content", body_status="completed",
              created_at="2026-05-26T01:00:00+00:00")
    target = _pick_representative([a, b])
    assert target["slug"] == "url__item__bbb"


def test_pick_representative_multiple_external() -> None:
    """external_id wiki 가 여러 개 + 같은 조건 — 가장 오래된 것 (body 길이 같음)."""
    same_body = "shared body content"
    yt = _wiki("yt__abc", body=same_body, body_status="completed",
               created_at="2026-05-26T01:00:00+00:00")
    arxiv = _wiki("arxiv__1234.5678", body=same_body, body_status="completed",
                  created_at="2026-05-26T02:00:00+00:00")
    self_w = _wiki("url__item__zzz", body=same_body, body_status="completed",
                   created_at="2026-05-25T00:00:00+00:00")  # self 가 더 오래됐어도
    target = _pick_representative([self_w, yt, arxiv])
    # external 우선 → yt vs arxiv 중 더 오래된 것 (yt 가 1시간 빠름)
    assert target["slug"] == "yt__abc"


# ─────────────────────────────────────────────────────────────────────────────
# placeholder titles
# ─────────────────────────────────────────────────────────────────────────────


def test_placeholder_titles_present() -> None:
    """기본 placeholder 들이 포함됐는지."""
    assert "Social Media Title Tag" in _PLACEHOLDER_TITLES
    assert "Abstract" in _PLACEHOLDER_TITLES
    assert "[no-title]" in _PLACEHOLDER_TITLES
    assert "paper_title" in _PLACEHOLDER_TITLES
