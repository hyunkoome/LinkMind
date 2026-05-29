"""
키워드 정규화 (2026-05-29, 사용자 규칙).

규칙:
  1. **영문 only** — 중국어(CJK)/일본어(かな·漢字)/한글 포함 키워드는 삭제 (None).
  2. **소문자-대시 slug** — 단어 경계를 '-' 로:
       - camelCase: `CloudCompare` → `cloud-compare`
       - 약어+단어: `TreeAIBox` → `tree-ai-box` (AI + Box 분리)
       - 공백/구분자(_/ 등): `Cloud Compare` → `cloud-compare`
  3. **알려진 약어** (split 예외): LiDAR→lidar, GitHub→github, IoT→iot 처럼
     mixed-case 약어는 통째로 한 토큰. (camelCase 규칙이 li-dar 로 쪼개는 걸 방지.)
  4. **별칭(alias)** — 정규화된 slug 를 canonical 로 통합: 3d-gaussian-splatting → 3dgs.
  5. **중복 제거** — 정규화 후 같아진 키워드는 dedup (순서 보존).

약어 목록 + 별칭은 **런타임 설정** (app_settings, Settings 페이지에서 편집).
set_keyword_config() 로 갱신. DB override 없으면 아래 DEFAULT_* 사용.
"""

from __future__ import annotations

import re
from typing import Iterable

# 중국어(CJK 통합/확장A/호환) + 일본어(히라가나·가타카나) + 한글(완성형·자모).
_CJK_HANGUL_RE = re.compile(
    "[぀-ヿ"      # Hiragana, Katakana
    "㐀-䶿"       # CJK Ext-A
    "一-鿿"       # CJK Unified
    "豈-﫿"       # CJK Compatibility Ideographs
    "가-힣"       # Hangul Syllables
    "ᄀ-ᇿ]"      # Hangul Jamo
)

# camelCase 경계 두 종.
_CAMEL_LOWER_UPPER = re.compile(r"(?<=[a-z])(?=[A-Z])")        # cloud|Compare
_CAMEL_ACRONYM = re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])")      # AI|Box

_NON_SLUG = re.compile(r"[^a-z0-9]+")
_ALIAS_SEP_RE = re.compile(r"\s*(?:=|->|→)\s*")   # "from = to" / "from -> to"

_MAX_LEN = 80

# ── 기본 설정 (DB override 없을 때) ──
# 약어: mixed-case 라 camelCase 규칙이 쪼개는 것들. 정식 표기로 적으면 split 패턴
# 자동 계산 (전부 대문자 약어 AI/API 등은 안 쪼개져 불필요).
DEFAULT_ACRONYMS: tuple[str, ...] = (
    "LiDAR", "GitHub", "GitLab", "IoT", "KiCAD", "CMake", "ChatGPT",
    "OpenAI", "OpenCV", "GraphQL", "WebGL", "WebGPU", "PyTorch",
    "TensorFlow", "NumPy", "SciPy", "macOS", "iOS", "iPadOS", "iPhone",
    "iPad", "NeRF", "PostgreSQL", "MongoDB", "MLOps", "DevOps", "YouTube",
    "DeepSeek", "DeepMind", "LangChain", "HuggingFace", "OpenGL",
)
# 별칭: 정규화된 slug → canonical. 구문/표기 변형 통합.
DEFAULT_ALIASES: dict[str, str] = {
    "3d-gaussian-splatting": "3dgs",
    "3d-gs": "3dgs",
}

# ── 런타임 설정 상태 (set_keyword_config 로 갱신) ──
_acronyms_source: list[str] = list(DEFAULT_ACRONYMS)
_aliases: dict[str, str] = dict(DEFAULT_ALIASES)
_acronym_merge: dict[tuple[str, ...], str] = {}     # split 토큰 시퀀스 → 합친 형태
_max_acronym_tokens: int = 1


def _split_tokens(s: str) -> list[str]:
    """camelCase/약어/공백/구분자 → 소문자 토큰 리스트 (약어 병합 전 단계)."""
    s = _CAMEL_LOWER_UPPER.sub("-", s)
    s = _CAMEL_ACRONYM.sub("-", s)
    s = _NON_SLUG.sub("-", s.lower()).strip("-")
    return [t for t in s.split("-") if t]


def _build_acronym_merge(acronyms: Iterable[str]) -> dict[tuple[str, ...], str]:
    """약어 정식 표기 → split 토큰 시퀀스 → 합친 canonical 의 매핑 구성.

    예: "LiDAR" → ("li","dar") → "lidar". 안 쪼개지는 약어(전부 대문자)는 제외.
    """
    m: dict[tuple[str, ...], str] = {}
    for a in acronyms:
        toks = tuple(_split_tokens(a))
        if len(toks) > 1:
            m[toks] = re.sub(r"[^a-z0-9]", "", a.lower())
    return m


def set_keyword_config(
    acronyms: Iterable[str] | None = None,
    aliases: dict[str, str] | None = None,
) -> None:
    """약어/별칭 설정 갱신 (런타임). None 이면 해당 항목은 DEFAULT 로.

    runtime_settings 가 app_settings(DB) 로딩 후 호출. normalize_keyword 가 즉시 반영.
    """
    global _acronyms_source, _aliases, _acronym_merge, _max_acronym_tokens
    _acronyms_source = list(acronyms) if acronyms is not None else list(DEFAULT_ACRONYMS)
    _aliases = dict(aliases) if aliases is not None else dict(DEFAULT_ALIASES)
    _acronym_merge = _build_acronym_merge(_acronyms_source)
    _max_acronym_tokens = max((len(k) for k in _acronym_merge), default=1)


def _merge_acronyms(tokens: list[str]) -> list[str]:
    """토큰 시퀀스에서 알려진 약어 split 을 다시 합침 (greedy, longest-first)."""
    out: list[str] = []
    i, n = 0, len(tokens)
    while i < n:
        merged = False
        for k in range(min(_max_acronym_tokens, n - i), 1, -1):
            seq = tuple(tokens[i:i + k])
            if seq in _acronym_merge:
                out.append(_acronym_merge[seq])
                i += k
                merged = True
                break
        if not merged:
            out.append(tokens[i])
            i += 1
    return out


def _slug_no_alias(raw: str) -> str | None:
    """별칭 적용 전 단계의 정규화 slug (split + 약어 병합). CJK/빈값 → None."""
    if not raw:
        return None
    s = raw.strip()
    if not s or _CJK_HANGUL_RE.search(s):
        return None
    tokens = _merge_acronyms(_split_tokens(s))
    if not tokens:
        return None
    return "-".join(tokens)[:_MAX_LEN].strip("-") or None


def normalize_keyword(raw: str) -> str | None:
    """단일 키워드 정규화. 영문 아니면(CJK/한글 포함) None, 빈 결과도 None.

    알려진 약어는 통째 유지(lidar), 별칭은 canonical 로 통합(3d-gaussian-splatting→3dgs).
    """
    slug = _slug_no_alias(raw)
    if slug is None:
        return None
    return _aliases.get(slug, slug) or None      # 별칭 1-hop


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


# ── Settings 텍스트 ⇄ 설정 (한 줄당 항목) ──

def parse_acronyms(text: str) -> list[str]:
    """약어 텍스트(한 줄당 하나, # 주석/빈 줄 무시) → 리스트."""
    out: list[str] = []
    for line in (text or "").splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            out.append(s)
    return out


def parse_aliases(text: str) -> dict[str, str]:
    """별칭 텍스트('from = to' / 'from -> to' 한 줄당) → dict. 양쪽 정규화(별칭 제외)."""
    out: dict[str, str] = {}
    for line in (text or "").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        parts = _ALIAS_SEP_RE.split(s, maxsplit=1)
        if len(parts) != 2:
            continue
        frm = _slug_no_alias(parts[0])
        to = _slug_no_alias(parts[1])
        if frm and to and frm != to:
            out[frm] = to
    return out


def format_acronyms() -> str:
    """현재 약어 설정 → 텍스트 (Settings 표시용)."""
    return "\n".join(_acronyms_source)


def format_aliases() -> str:
    """현재 별칭 설정 → 텍스트 ('from = to' 한 줄당)."""
    return "\n".join(f"{k} = {v}" for k, v in _aliases.items())


# 모듈 로드 시 기본값으로 초기화 (runtime_settings 가 DB override 로 덮어씀).
set_keyword_config()
