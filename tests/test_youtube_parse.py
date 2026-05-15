"""
backend.ingest.youtube.parse_youtube_url 단위 테스트.

watch / shorts / embed / youtu.be / playlist / video+list 모든 변형 케이스.
"""

from __future__ import annotations

from backend.ingest.youtube import parse_youtube_url


def test_watch_video():
    r = parse_youtube_url("https://www.youtube.com/watch?v=PYr-LSOf2OY")
    assert r["kind"] == "video"
    assert r["video_id"] == "PYr-LSOf2OY"
    assert r["playlist_id"] is None


def test_watch_video_with_list_prefers_playlist():
    """v=... 와 list=... 둘 다 있으면 playlist 우선 (영상보다 list 가 더 큰 단위)."""
    r = parse_youtube_url("https://www.youtube.com/watch?v=PYr-LSOf2OY&list=PL5Q2soXY")
    assert r["kind"] == "playlist"
    assert r["playlist_id"] == "PL5Q2soXY"
    # video_id 는 추가 정보로 보존
    assert r["video_id"] == "PYr-LSOf2OY"


def test_playlist_only():
    r = parse_youtube_url("https://www.youtube.com/playlist?list=PL5Q2soXY")
    assert r["kind"] == "playlist"
    assert r["playlist_id"] == "PL5Q2soXY"
    assert r["video_id"] is None


def test_short_url_youtu_be():
    r = parse_youtube_url("https://youtu.be/PYr-LSOf2OY")
    assert r["kind"] == "video"
    assert r["video_id"] == "PYr-LSOf2OY"


def test_short_url_with_playlist():
    r = parse_youtube_url("https://youtu.be/PYr-LSOf2OY?list=PL5Q2soXY")
    assert r["kind"] == "playlist"
    assert r["playlist_id"] == "PL5Q2soXY"
    assert r["video_id"] == "PYr-LSOf2OY"


def test_shorts_path():
    r = parse_youtube_url("https://www.youtube.com/shorts/abc12345xyz")
    assert r["kind"] == "video"
    assert r["video_id"] == "abc12345xyz"


def test_embed_path():
    r = parse_youtube_url("https://www.youtube.com/embed/abc12345xyz")
    assert r["kind"] == "video"
    assert r["video_id"] == "abc12345xyz"


def test_m_youtube_host():
    r = parse_youtube_url("https://m.youtube.com/watch?v=abc12345xyz")
    assert r["kind"] == "video"
    assert r["video_id"] == "abc12345xyz"


def test_non_youtube_host_returns_unknown():
    r = parse_youtube_url("https://vimeo.com/123456")
    assert r["kind"] == "unknown"
    assert r["video_id"] is None
    assert r["playlist_id"] is None
