"""
URL host 분류 + hashtag/tag 정규화 단위 테스트.

이번 세션 변경분의 핵심 흐름인 'host 자동 라우팅 + LLM 해시태그 → final_tags' 가
edge case 에서 깨지지 않는지 확인.
"""

from __future__ import annotations

from backend.api.ingest import _classify_url
from backend.ingest.url import _extract_hashtags, _normalize_tags


# ── _classify_url ───────────────────────────────────────────


def test_classify_youtube_variants():
    assert _classify_url("https://www.youtube.com/watch?v=abc") == "youtube"
    assert _classify_url("https://youtube.com/playlist?list=PLfoo") == "youtube"
    assert _classify_url("https://youtu.be/abc") == "youtube"
    assert _classify_url("https://m.youtube.com/watch?v=abc") == "youtube"


def test_classify_github():
    assert _classify_url("https://github.com/owner/repo") == "github"
    assert _classify_url("https://www.github.com/owner/repo") == "github"
    # gist 등 다른 host 는 일반 url 로
    assert _classify_url("https://gist.github.com/owner/abcd") == "url"


def test_classify_pdf_extension():
    assert _classify_url("https://example.com/paper.pdf") == "pdf"
    # querystring 있어도 확장자 매칭
    assert _classify_url("https://example.com/paper.pdf?download=1") == "pdf"
    # 확장자 없으면 일반 url
    assert _classify_url("https://example.com/paper") == "url"


def test_classify_fallback_url():
    assert _classify_url("https://arxiv.org/abs/2106.09685") == "url"
    assert _classify_url("https://en.wikipedia.org/wiki/SLAM") == "url"


# ── _extract_hashtags / _normalize_tags ─────────────────────


def test_extract_hashtags_basic():
    text = "한국어 본문 끝에 #SLAM #3DGS #LowRankAdaptation"
    assert _extract_hashtags(text) == ["SLAM", "3DGS", "LowRankAdaptation"]


def test_extract_hashtags_mixed_korean():
    text = "본문 ... #한글태그 #영어태그 #under_score #hyphen-tag"
    out = _extract_hashtags(text)
    assert "한글태그" in out
    assert "영어태그" in out
    assert "under_score" in out
    assert "hyphen-tag" in out


def test_extract_hashtags_empty():
    assert _extract_hashtags("") == []
    assert _extract_hashtags("no tags here") == []


def test_normalize_tags_case_insensitive_dedup():
    """첫 등장한 form 을 살리고 case-insensitive 로 dedup."""
    out = _normalize_tags(["SLAM", "slam", "Slam"])
    assert out == ["SLAM"]


def test_normalize_tags_strip_punctuation():
    out = _normalize_tags(["#tag,", "tag.", "  tag  "])
    # 모두 같은 'tag' 로 collapse, 첫 번째 form 유지 (lstrip # 후 strip).
    assert out == ["tag"]


def test_normalize_tags_drops_too_long():
    long_tag = "x" * 60
    out = _normalize_tags(["short", long_tag])
    assert "short" in out
    assert long_tag not in out


def test_normalize_tags_preserves_first_seen_form():
    """`MIT` 가 먼저 나오면 그 케이스 유지 (license tag 우선순위)."""
    out = _normalize_tags(["MIT", "mit", "MIT License"])
    assert out[0] == "MIT"
    # 'MIT License' 는 'MIT' 와 다른 string 이라 살아남음
    assert "MIT License" in out
