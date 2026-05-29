"""
키워드 정규화 단위 테스트 (2026-05-29, 사용자 규칙).

규칙: 영문 only (CJK/한글 삭제) + 소문자-대시 slug (camelCase·약어·공백 분리) + dedup.
pure 함수 → tests/ 직접 (cpu 마커 없음, §9 결정 흐름 1).
"""

from __future__ import annotations

import pytest

from backend.utils.keywords import normalize_keyword, normalize_keywords


@pytest.mark.parametrize(
    "raw,expected",
    [
        # ── 사용자 명시 예시 ──
        ("CloudCompare", "cloud-compare"),       # camelCase
        ("Cloud Compare", "cloud-compare"),      # 공백
        ("TreeAIBox", "tree-ai-box"),            # ★ 약어(AI)+단어(Box) 분리 ★
        ("PythonPlugin", "python-plugin"),
        ("QSM", "qsm"),                          # 전부 대문자 = 분리 없음
        ("WoodCls", "wood-cls"),
        ("3DTreeAlgorithms", "3d-tree-algorithms"),  # 숫자는 안 쪼갬 (3d 유지)
        # ── 일반 ──
        ("computer vision", "computer-vision"),
        ("LiDAR", "li-dar"),                     # 약어+소문자 → li|dar... 실제 'LiDAR' = Li+DAR
        ("already-dashed", "already-dashed"),
        ("Mixed_Underscore Case", "mixed-underscore-case"),
        ("  trailing space  ", "trailing-space"),
        ("UPPER", "upper"),
        ("HTMLParser", "html-parser"),           # 약어+단어
    ],
)
def test_normalize_keyword_examples(raw, expected):
    assert normalize_keyword(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "深度学习",        # 중국어
        "枝条分离",
        "骨架화",          # 한자+한글 혼합
        "3D体积估算",      # 숫자+중국어 → 삭제
        "한글키워드",      # 한글
        "ディープラーニング",  # 일본어 가타카나
        "",                # 빈 값
        "   ",             # 공백만
        "---",             # 순수 대시
        "...",             # 순수 구두점
    ],
)
def test_normalize_keyword_dropped(raw):
    """CJK/한글/일본어 포함 또는 빈 결과 → None (삭제)."""
    assert normalize_keyword(raw) is None


def test_normalize_keywords_dedup_order():
    """정규화 후 같아진 키워드 dedup + 순서 보존 + 삭제(None) 제외."""
    raws = [
        "CloudCompare",      # → cloud-compare
        "Cloud Compare",     # → cloud-compare (중복)
        "深度学习",           # → None (삭제)
        "Python Plugin",     # → python-plugin
        "cloud-compare",     # → cloud-compare (중복)
        "QSM",               # → qsm
    ]
    assert normalize_keywords(raws) == ["cloud-compare", "python-plugin", "qsm"]


def test_normalize_keywords_empty():
    assert normalize_keywords([]) == []
    assert normalize_keywords(None) == []  # type: ignore[arg-type]


def test_long_keyword_capped():
    """80자 초과는 cap (양끝 대시 정리)."""
    out = normalize_keyword("a" * 200)
    assert out is not None and len(out) <= 80
