"""
backend.ingest.arxiv — arxiv API 호출로 정확한 메타데이터 (title/abstract/authors).

배경 (사용자 명시 2026-05-26):
  - "https://arxiv.org/abs/2003.02014 이 들어오면 'Redesigning SLAM for Arbitrary
    Multi-Camera Systems' 가 타이틀로 등록되어야 한다"
  - 현재 ingest_url 의 readability/HTML title 추출은 arxiv abs 페이지에서 종종
    부정확 (예: "arxiv:2003.02014" 같은 slug-form)
  - arxiv API (export.arxiv.org/api/query) 는 권위 있는 메타라 100% 정확

사용:
  - backend/ingest/url/__init__.py 의 extract_doc 직후 hook
  - arxiv URL detect → arxiv_id 파싱 → fetch_arxiv_metadata → ExtractedDoc 의 title/
    abstract override

rate limit: arxiv API 는 ~3 req/sec. 한 번에 1개만 호출하니 무관.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from typing import Any

import httpx

logger = logging.getLogger("linkmind.ingest.arxiv")


_ARXIV_API = "https://export.arxiv.org/api/query"
_ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}

# arxiv URL 형식:
#   https://arxiv.org/abs/2003.02014
#   https://arxiv.org/abs/2003.02014v2
#   https://arxiv.org/pdf/2003.02014.pdf
#   https://arxiv.org/abs/cs.CV/0512066    (옛 형식)
#   https://ar5iv.labs.arxiv.org/html/2003.02014
_ARXIV_ID_RE = re.compile(
    r"arxiv\.org/(?:abs|pdf|html)/([a-z\-\.]+/)?(\d{4}\.\d{4,5}|[a-z\-]+/\d{7})",
    re.IGNORECASE,
)
_AR5IV_ID_RE = re.compile(
    r"ar5iv(?:\.labs)?\.arxiv\.org/(?:abs|html)/(\d{4}\.\d{4,5})",
    re.IGNORECASE,
)


def parse_arxiv_id(url: str) -> str | None:
    """URL 에서 arxiv id 추출 (예: '2003.02014'). 매칭 안 되면 None.

    .pdf 확장자도 OK — 자체 strip.
    """
    if not url:
        return None
    m = _AR5IV_ID_RE.search(url)
    if m:
        return m.group(1)
    m = _ARXIV_ID_RE.search(url)
    if m:
        # group(2) 가 실제 id. group(1) 은 옛 형식의 카테고리 prefix (cs.CV/ 등)
        arxiv_id = m.group(2)
        # .pdf strip
        return arxiv_id.removesuffix(".pdf")
    return None


def is_arxiv_url(url: str) -> bool:
    return parse_arxiv_id(url) is not None


async def fetch_arxiv_metadata(arxiv_id: str, *, timeout: float = 15.0) -> dict[str, Any] | None:
    """arxiv API 호출. id → {title, summary, authors, published, doi}.

    실패 (rate limit / 네트워크 / 매칭 없음) 시 None — caller 가 HTML title fallback.
    """
    if not arxiv_id:
        return None
    params = {"id_list": arxiv_id, "max_results": "1"}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(_ARXIV_API, params=params)
            r.raise_for_status()
            text_body = r.text
    except (httpx.HTTPError, httpx.TimeoutException) as e:
        logger.info("arxiv API 실패 (id=%s, %s) — fallback", arxiv_id, e)
        return None

    try:
        root = ET.fromstring(text_body)
    except ET.ParseError as e:
        logger.warning("arxiv API XML parse 실패 (id=%s): %s", arxiv_id, e)
        return None

    entries = root.findall("atom:entry", _ATOM_NS)
    if not entries:
        return None
    entry = entries[0]

    title_el = entry.find("atom:title", _ATOM_NS)
    sum_el = entry.find("atom:summary", _ATOM_NS)
    pub_el = entry.find("atom:published", _ATOM_NS)
    authors = [
        (a.find("atom:name", _ATOM_NS).text or "")
        for a in entry.findall("atom:author", _ATOM_NS)
        if a.find("atom:name", _ATOM_NS) is not None
    ]
    title = (title_el.text or "").strip().replace("\n", " ") if title_el is not None else None
    summary = (sum_el.text or "").strip().replace("\n", " ") if sum_el is not None else None
    published = (pub_el.text or "").strip() if pub_el is not None else None

    if not title:
        return None

    return {
        "arxiv_id": arxiv_id,
        "title": title,
        "summary": summary,
        "authors": authors,
        "published": published,
    }
