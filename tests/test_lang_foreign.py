"""
backend/utils/lang.py — 외국어(중국어/일본어) 감지 단위 테스트.

pure 함수라 마커 없음 (cpu 카테고리). 위키 본문 언어 안전장치의 핵심 로직.
"""

from __future__ import annotations

from backend.utils.lang import (
    FOREIGN_THRESHOLD,
    count_foreign_cjk,
    has_foreign_script,
)


def test_korean_only_no_foreign():
    """순수 한국어 + 영문 기술용어는 외국어 0."""
    text = "이것은 한국어 위키 본문입니다. LoRA 와 ROS2, Isaac Sim 을 사용합니다."
    assert count_foreign_cjk(text) == 0
    assert not has_foreign_script(text)


def test_chinese_sentence_detected():
    """중국어 문장은 임계 초과로 외국어 감지."""
    text = "该项目使用开源训练及部署代码，使得用户能够轻松实现机器人的行走和跑步功能。"
    assert count_foreign_cjk(text) > FOREIGN_THRESHOLD
    assert has_foreign_script(text)


def test_japanese_kana_detected():
    """일본어 가나(히라가나/가타카나)도 감지."""
    text = "これはにほんごのテキストです。ロボットのうごきをせいぎょする。"
    assert count_foreign_cjk(text) > FOREIGN_THRESHOLD
    assert has_foreign_script(text)


def test_few_hanja_allowed():
    """한국어에 한자 몇 자(고유명사 등) 섞인 정도는 임계 이하 → 정상으로 취급."""
    text = "자율주행(自律走行)은 실시간(實時間) 처리가 중요한 기술이다."
    # 自律走行 + 實時間 = 7 자 (FOREIGN_THRESHOLD=8 이하)
    assert count_foreign_cjk(text) == 7
    assert not has_foreign_script(text)


def test_hangul_not_counted():
    """한글 음절은 외국어로 세지 않음 ('가나다' 의 '가나'는 한글이지 일본어 가나 아님)."""
    assert count_foreign_cjk("한글만 잔뜩 있는 문장 가나다라마바사") == 0


def test_empty_and_none_safe():
    assert count_foreign_cjk("") == 0
    assert count_foreign_cjk(None) == 0  # type: ignore[arg-type]
    assert not has_foreign_script("")


def test_threshold_boundary():
    """임계값 경계 — 정확히 threshold 면 통과, +1 이면 외국어."""
    at_limit = "字" * FOREIGN_THRESHOLD
    over_limit = "字" * (FOREIGN_THRESHOLD + 1)
    assert not has_foreign_script(at_limit)
    assert has_foreign_script(over_limit)


def test_custom_threshold():
    """threshold 인자 override 동작."""
    text = "字字字"
    assert not has_foreign_script(text, threshold=5)
    assert has_foreign_script(text, threshold=2)
