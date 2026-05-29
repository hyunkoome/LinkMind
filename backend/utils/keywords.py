"""
키워드 정규화 (2026-05-29, 사용자 규칙).

규칙:
  1. **영문 only** — 중국어(CJK)/일본어(かな·漢字)/한글 포함 키워드는 삭제 (None).
  2. **소문자-대시 slug** — 단어 경계를 '-' 로:
       - camelCase: `CloudCompare` → `cloud-compare`
       - 약어+단어: `TreeAIBox` → `tree-ai-box` (AI + Box 분리)
       - 공백/구분자(_/ 등): `Cloud Compare` → `cloud-compare`
  3. **중복 제거** — 정규화 후 같아진 키워드는 dedup (순서 보존).

예:
  CloudCompare      → cloud-compare
  Cloud Compare     → cloud-compare
  TreeAIBox         → tree-ai-box
  PythonPlugin      → python-plugin
  QSM               → qsm
  3DTreeAlgorithms  → 3d-tree-algorithms
  深度学习 / 한글     → None (삭제)
"""

from __future__ import annotations

import re
from typing import Iterable

# 중국어(CJK 통합/확장A/호환) + 일본어(히라가나·가타카나) + 한글(완성형·자모).
# 하나라도 포함되면 '영문 아님' 으로 보고 키워드 삭제.
_CJK_HANGUL_RE = re.compile(
    "[぀-ヿ"      # Hiragana, Katakana
    "㐀-䶿"       # CJK Ext-A
    "一-鿿"       # CJK Unified
    "豈-﫿"       # CJK Compatibility Ideographs
    "가-힣"       # Hangul Syllables
    "ᄀ-ᇿ]"      # Hangul Jamo
)

# camelCase 경계 두 종 — 순서대로 적용.
_CAMEL_LOWER_UPPER = re.compile(r"(?<=[a-z])(?=[A-Z])")        # cloud|Compare, e|A
_CAMEL_ACRONYM = re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])")      # AI|Box, HTML|Parser

_NON_SLUG = re.compile(r"[^a-z0-9]+")

_MAX_LEN = 80


def normalize_keyword(raw: str) -> str | None:
    """단일 키워드 정규화. 영문 아니면(CJK/한글 포함) None, 빈 결과도 None."""
    if not raw:
        return None
    s = raw.strip()
    if not s or _CJK_HANGUL_RE.search(s):
        return None
    # camelCase / 약어 경계에 대시 삽입 (소문자화 전에 — 대소문자 정보 사용).
    s = _CAMEL_LOWER_UPPER.sub("-", s)
    s = _CAMEL_ACRONYM.sub("-", s)
    s = s.lower()
    # 영숫자 아닌 모든 것(공백/_/슬래시/구두점/삽입된 대시) → 단일 대시 + 양끝 제거.
    s = _NON_SLUG.sub("-", s).strip("-")
    if not s:
        return None
    return s[:_MAX_LEN].strip("-") or None


def normalize_keywords(raws: Iterable[str]) -> list[str]:
    """키워드 리스트 정규화 + 중복 제거 (순서 보존). 삭제(None) 는 제외."""
    out: list[str] = []
    seen: set[str] = set()
    for r in raws or []:
        n = normalize_keyword(r)
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out
