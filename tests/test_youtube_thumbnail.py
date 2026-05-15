"""
backend.ingest.youtube._pick_best_thumbnail 단위 테스트.

yt-dlp info dict 의 다양한 thumbnail 표현을 처리하는지 확인.
"""

from __future__ import annotations

from backend.ingest.youtube import _pick_best_thumbnail


def test_pick_highest_resolution_when_sized():
    info = {"thumbnails": [
        {"url": "a", "width": 120, "height": 90},
        {"url": "b", "width": 1280, "height": 720},
        {"url": "c", "width": 480, "height": 360},
    ]}
    assert _pick_best_thumbnail(info)["url"] == "b"


def test_pick_last_when_no_size_meta():
    """크기 메타 없는 경우 yt-dlp 가 보통 마지막을 best 로 두므로 마지막 url 항목 선택."""
    info = {"thumbnails": [{"url": "a"}, {"url": "b"}]}
    assert _pick_best_thumbnail(info)["url"] == "b"


def test_skips_empty_url_entries():
    """url 누락된 항목은 무시."""
    info = {"thumbnails": [{"url": ""}, {"url": "a"}, {}]}
    assert _pick_best_thumbnail(info)["url"] == "a"


def test_falls_back_to_single_thumbnail_field():
    info = {"thumbnail": "https://i.ytimg.com/vi/x/default.jpg"}
    out = _pick_best_thumbnail(info)
    assert out["url"] == "https://i.ytimg.com/vi/x/default.jpg"
    # 단일 필드는 width/height 가 없음
    assert out["width"] is None
    assert out["height"] is None


def test_returns_none_when_no_thumbnail_at_all():
    assert _pick_best_thumbnail({}) is None
    assert _pick_best_thumbnail({"thumbnails": []}) is None
    assert _pick_best_thumbnail({"thumbnails": [{}]}) is None


def test_prefers_sized_over_unsized_when_mixed():
    """sized 가 한 개라도 있으면 그 안에서 max. unsized 는 무시."""
    info = {"thumbnails": [
        {"url": "u"},                              # unsized — 무시
        {"url": "s", "width": 320, "height": 180}, # sized
    ]}
    assert _pick_best_thumbnail(info)["url"] == "s"
