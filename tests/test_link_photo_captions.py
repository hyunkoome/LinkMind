"""
link_photo_captions 의 caption→대상 위키 slug 역추적 단위 테스트 (2026-05-29).

photo 의 텔레그램 caption(URL) 을 그 URL 콘텐츠의 위키 slug 로 정규화 매핑.
youtu.be↔youtube.com 등 정규화 무시하고 external_id 로. pure 함수 (cpu, §9).
"""

from __future__ import annotations

from backend.jobs.link_photo_captions import _is_url, _target_slug


def test_is_url():
    assert _is_url("https://github.com/a/b")
    assert _is_url("http://x.com")
    assert not _is_url("그냥 메모")
    assert not _is_url(None)
    assert not _is_url("")


def test_github_caption_to_github_wiki():
    assert _target_slug("https://github.com/VK-Ant/adaptive-intelligence", {}) == \
        "github__vk-ant-adaptive-intelligence"


def test_youtube_short_url_normalizes_to_yt_wiki():
    # youtu.be/<id> → yt__<id> (youtube.com 과 동일 slug 로 정규화)
    assert _target_slug("https://youtu.be/cDYIdId3XSY?si=zT99", {}) == "yt__cdyidid3xsy"
    assert _target_slug("https://www.youtube.com/watch?v=cDYIdId3XSY", {}) == "yt__cdyidid3xsy"


def test_arxiv_caption_to_arxiv_wiki():
    assert _target_slug("https://arxiv.org/abs/2106.09685", {}) == "arxiv__2106.09685"


def test_plain_url_uses_matching_item_self_wiki():
    """external_id 없는 url 은 같은 source_url 로 ingest 된 item 의 self_wiki."""
    url = "https://someblog.com/post"
    out = _target_slug(url, {url: "abc-123"})
    assert out == "url__item__abc-123"


def test_plain_url_no_match_is_none():
    assert _target_slug("https://someblog.com/post", {}) is None
