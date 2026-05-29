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

# 알려진 약어/브랜드 (2026-05-29, 사용자 요청) — mixed-case 라 위 camelCase 규칙이
# 원치 않게 쪼개는 것들. 통째로 한 토큰 유지: LiDAR→lidar, GitHub→github, IoT→iot.
# (AI/API/GPU 처럼 전부 대문자인 약어는 내부 경계가 없어 자동으로 안 쪼개짐 — 불필요.)
# 새 약어는 정식 표기(예: "WebGPU") 그대로 추가하면 됨 — split 패턴은 자동 계산.
_KNOWN_ACRONYMS: tuple[str, ...] = (
    "LiDAR", "GitHub", "GitLab", "IoT", "KiCAD", "ChatGPT", "OpenAI",
    "OpenCV", "GraphQL", "WebGL", "WebGPU", "PyTorch", "TensorFlow",
    "NumPy", "SciPy", "macOS", "iOS", "iPadOS", "iPhone", "iPad",
    "NeRF", "PostgreSQL", "MongoDB", "MLOps", "DevOps", "YouTube",
    "DeepSeek", "DeepMind", "LangChain", "HuggingFace", "OpenGL",
)


def _split_tokens(s: str) -> list[str]:
    """camelCase/약어/공백/구분자 → 소문자 토큰 리스트 (약어 병합 전 단계)."""
    s = _CAMEL_LOWER_UPPER.sub("-", s)
    s = _CAMEL_ACRONYM.sub("-", s)
    s = _NON_SLUG.sub("-", s.lower()).strip("-")
    return [t for t in s.split("-") if t]


# 약어가 _split_tokens 로 쪼개졌을 때의 토큰 시퀀스 → 합친 canonical 형태.
# 예: "LiDAR" → ("li","dar") → "lidar". 모듈 로드 시 1회 계산.
_ACRONYM_MERGE: dict[tuple[str, ...], str] = {}
for _a in _KNOWN_ACRONYMS:
    _toks = tuple(_split_tokens(_a))
    if len(_toks) > 1:        # 안 쪼개지는 약어(전부 대문자 등)는 처리 불필요
        _ACRONYM_MERGE[_toks] = re.sub(r"[^a-z0-9]", "", _a.lower())
_MAX_ACRONYM_TOKENS = max((len(k) for k in _ACRONYM_MERGE), default=1)


def _merge_acronyms(tokens: list[str]) -> list[str]:
    """토큰 시퀀스에서 알려진 약어 split 을 다시 합침 (greedy, longest-first)."""
    out: list[str] = []
    i, n = 0, len(tokens)
    while i < n:
        merged = False
        for k in range(min(_MAX_ACRONYM_TOKENS, n - i), 1, -1):
            seq = tuple(tokens[i:i + k])
            if seq in _ACRONYM_MERGE:
                out.append(_ACRONYM_MERGE[seq])
                i += k
                merged = True
                break
        if not merged:
            out.append(tokens[i])
            i += 1
    return out


def normalize_keyword(raw: str) -> str | None:
    """단일 키워드 정규화. 영문 아니면(CJK/한글 포함) None, 빈 결과도 None.

    알려진 약어(LiDAR/GitHub/IoT 등)는 통째로 유지 (li-dar 아니라 lidar).
    """
    if not raw:
        return None
    s = raw.strip()
    if not s or _CJK_HANGUL_RE.search(s):
        return None
    tokens = _merge_acronyms(_split_tokens(s))
    if not tokens:
        return None
    return "-".join(tokens)[:_MAX_LEN].strip("-") or None


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
