"""user_notes 의 자유 문체에서 LLM 으로 키워드 추출.

배경 (CLAUDE.md §1, Phase 2.5):
- 사용자가 PDF/논문 등에 자유 문체로 메모 (예: "포인트클라우드 압축시 활용,
  그리네타 개발시 필요할듯") 적으면 그 메모는 first-class 학습 데이터다.
- 한국어 자유 문체이므로 hashtag (`#`) 강제 불가 — LLM 으로 zero-shot 키워드 추출.
- 추출 키워드는 items.tags 에 병합 → 기존 검색/graph 흐름 (tag filter, topic
  자동 link) 활용.

설계:
- PATCH /items/{id} endpoint 안에서 BackgroundTask 로 비동기 호출 — PATCH 응답은
  즉시, 키워드 갱신은 백그라운드 (LLM 호출 ~수십 초).
- 호출 실패/타임아웃 시 [] 반환 — PATCH endpoint 가 graceful 처리.
- 짧은 메모 (< _MIN_NOTES_LENGTH) 는 호출 X (의미 없음).
"""

from __future__ import annotations

import asyncio
import logging
import re

from backend.llm.base import ChatMessage
from backend.llm.factory import get_llm_provider

logger = logging.getLogger(__name__)


_KEYWORD_SYSTEM = """사용자가 작성한 메모/아이디어에서 핵심 키워드를 추출하라.

출력 규칙:
- 한국어 또는 영어 단어/짧은 구 (최대 4단어, 30자 이내)
- 6개 이상 10개 이하
- 쉼표(,)로 구분, 다른 문장이나 설명 절대 없이 키워드만
- 의미가 같은 키워드 중복 X (예: 3DGS / 3D Gaussian Splatting 중 하나만)
- 너무 일반적인 단어 (사용/방법/필요) 는 빼고 구체적인 기술/주제어"""


_MIN_NOTES_LENGTH = 10   # 너무 짧은 메모는 LLM 호출 무의미
_INPUT_CAP = 2000        # LLM 입력 길이 cap
_KEYWORD_MAX = 10
_KEYWORD_MIN_COUNT = 1   # 응답 파싱 후 최소 이 개수는 있어야 valid
_LIST_MARKER_RE = re.compile(r"^(?:[\d]+[.)]|[-*•])\s*")


def _normalize_keyword(s: str) -> str:
    """LLM 응답의 list marker / 따옴표 / 공백 제거."""
    s = s.strip()
    s = _LIST_MARKER_RE.sub("", s)
    s = s.strip('"\'"\'“”「」『』').strip()
    return s


def _parse_keywords(raw: str) -> list[str]:
    """LLM raw 응답 → 정규화된 키워드 list (dedup 적용)."""
    parts = re.split(r"[,\n]", raw.strip())
    keywords: list[str] = []
    seen: set[str] = set()
    for p in parts:
        k = _normalize_keyword(p)
        if not k or len(k) > 30:
            continue
        if k.lower() in seen:
            continue
        seen.add(k.lower())
        keywords.append(k)
        if len(keywords) >= _KEYWORD_MAX:
            break
    return keywords


async def extract_keywords_from_notes(
    user_notes: str | None, *, timeout: float = 90.0,
) -> list[str]:
    """user_notes 에서 키워드 list 반환.

    Args:
        user_notes: 사용자 메모. None / 빈 / 너무 짧으면 [] 반환.
        timeout: LLM 응답 wait 최대 초. 초과 시 [] 반환.

    Returns:
        키워드 list. 최대 _KEYWORD_MAX 개. 호출/파싱 실패 시 [].
    """
    if not user_notes or len(user_notes.strip()) < _MIN_NOTES_LENGTH:
        return []

    provider = get_llm_provider()
    messages = [
        ChatMessage(role="system", content=_KEYWORD_SYSTEM),
        ChatMessage(role="user", content=user_notes[:_INPUT_CAP]),
    ]

    try:
        resp = await asyncio.wait_for(
            provider.chat(messages, temperature=0.2, max_tokens=200),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger.warning("키워드 추출 LLM timeout (%.0fs) — user_notes len=%d", timeout, len(user_notes))
        return []
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "키워드 추출 LLM 실패 (%s): %s", type(e).__name__, e or "<empty msg>",
        )
        return []

    keywords = _parse_keywords(resp.text)
    if len(keywords) < _KEYWORD_MIN_COUNT:
        logger.info("키워드 추출 결과 비어있음 — raw=%r", resp.text[:200])
        return []

    logger.info(
        "키워드 추출 성공 — %d 개: %s",
        len(keywords), ", ".join(keywords),
    )
    return keywords
