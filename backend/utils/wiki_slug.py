"""topics.slug → wiki_pages.slug 정규화 — D10 wiki 모듈 공용.

topics 의 slug 는 'arxiv:2106.09685' / 'yt:abc123' / 'github:owner-repo' /
'url:item:<uuid>' 형태 (콜론 구분). wiki_pages 의 slug 는 URL path 에 쓰일 수
있게 콜론 → '__', 공백/슬래시 → '-', 그 외 비ascii/특수문자 정리.

wave-1g backfill (`wiki_backfill_from_topics.py`) 와 신규 ingest 의 classifier
보장 (2026-05-27, D11) 가 같은 함수 써야 slug 가 동일 — backfill 자료와 신규
자료가 wiki_pages 에서 충돌 없이 합쳐짐 (ON CONFLICT 매칭).
"""

from __future__ import annotations

import re


_NON_SLUG_CHARS_RE = re.compile(r"[^a-z0-9가-힣._\-]+")
_MULTI_DASH_RE = re.compile(r"-+")


def sanitize_wiki_slug(raw: str) -> str:
    """topics.slug → wiki_pages.slug 변환.

    예:
      'arxiv:2106.09685'           → 'arxiv__2106.09685'
      'yt:Byo7yew9-OQ'             → 'yt__byo7yew9-oq'
      'github:facebookresearch/sam'→ 'github__facebookresearch-sam'
      'url:item:<uuid>'            → 'url__item__<uuid>' (self_wiki fallback)
      ''                            → 'untitled'

    한글은 wiki URL 에 OK 형태로 보존 (사용자 한국어 카테고리 자료).
    """
    if not raw:
        return "untitled"
    s = raw.strip().lower()
    s = s.replace(":", "__").replace("/", "-").replace(" ", "-")
    s = _NON_SLUG_CHARS_RE.sub("-", s)
    s = _MULTI_DASH_RE.sub("-", s).strip("-_")
    return s[:200] if s else "untitled"
