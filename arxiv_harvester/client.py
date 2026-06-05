"""arxiv_harvester.client — arxiv API(Atom) 검색 클라이언트.

키워드/쿼리 → arxiv `export.arxiv.org/api/query` 호출 → ArxivPaper 목록.
LinkMind 비의존 (독립 패키지). 실패(네트워크/parse/빈쿼리)는 빈 리스트로 graceful.

rate limit: arxiv API 는 ~3 req/sec. 검색은 쿼리당 1회라 무관.
"""
from __future__ import annotations

import asyncio
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime

import httpx

from arxiv_harvester.models import ArxivPaper

logger = logging.getLogger("arxiv_harvester.client")

_ARXIV_API = "https://export.arxiv.org/api/query"
_ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}

# arxiv 는 User-Agent 없는/봇 같은 요청을 더 강하게 rate limit 한다. 식별 가능한 UA 를
# 보내 정책을 준수한다 (arxiv API 권장: 앱 이름 + 연락 URL).
_HEADERS = {
    "User-Agent": "arxiv-harvester/0.1 (+https://github.com/hyunkoome/arxiv-harvester)",
}

# arxiv id 추출 (abs/pdf/html URL 또는 ar5iv). 신형(2003.02014) + 구형(cs.CV/0512066).
_ARXIV_ID_RE = re.compile(
    r"arxiv\.org/(?:abs|pdf|html)/([a-z\-\.]+/)?(\d{4}\.\d{4,5}|[a-z\-]+/\d{7})",
    re.IGNORECASE,
)
_AR5IV_ID_RE = re.compile(
    r"ar5iv(?:\.labs)?\.arxiv\.org/(?:abs|html)/(\d{4}\.\d{4,5})",
    re.IGNORECASE,
)

_SORT_VALUES = {"relevance", "lastUpdatedDate", "submittedDate"}


class RateLimitError(Exception):
    """arxiv API rate limit (HTTP 429). 호출자가 '결과 없음' 과 구분해 안내하도록 별도 예외.

    arxiv export API 는 짧은 시간에 여러 번 호출하면 429 'Rate exceeded' 를 준다
    (~1 req/3sec 권장). 잠시 후 재시도하면 풀린다.
    """



def parse_arxiv_id(url: str) -> str | None:
    """URL 에서 arxiv id 추출 (예: '2003.02014'). 매칭 안 되면 None. .pdf 자체 strip."""
    if not url:
        return None
    m = _AR5IV_ID_RE.search(url)
    if m:
        return m.group(1)
    m = _ARXIV_ID_RE.search(url)
    if m:
        return m.group(2).removesuffix(".pdf")
    return None


def _parse_published(raw: str | None) -> datetime | None:
    """arxiv published 문자열(ISO, 예: '2021-06-17T17:37:18Z') → datetime. 실패 None."""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_entry(entry: ET.Element) -> ArxivPaper | None:
    """Atom <entry> → ArxivPaper. title 없으면 None (불완전 entry skip).

    categories 는 <category term="cs.CV"/> 들에서 term 속성을 수집 (필터용 — 기존
    backend/ingest/arxiv 에는 없던 추가).
    """
    title_el = entry.find("atom:title", _ATOM_NS)
    title = (title_el.text or "").strip().replace("\n", " ") if title_el is not None else ""
    if not title:
        return None

    sum_el = entry.find("atom:summary", _ATOM_NS)
    pub_el = entry.find("atom:published", _ATOM_NS)
    id_el = entry.find("atom:id", _ATOM_NS)

    summary = (sum_el.text or "").strip().replace("\n", " ") if sum_el is not None else ""
    authors = [
        (a.find("atom:name", _ATOM_NS).text or "")
        for a in entry.findall("atom:author", _ATOM_NS)
        if a.find("atom:name", _ATOM_NS) is not None
    ]
    categories = [
        c.get("term", "")
        for c in entry.findall("atom:category", _ATOM_NS)
        if c.get("term")
    ]

    abs_url = (id_el.text or "").strip() if id_el is not None else ""
    arxiv_id = parse_arxiv_id(abs_url) or ""
    if abs_url.startswith("http://"):
        abs_url = "https://" + abs_url[len("http://") :]
    pdf_url = f"https://arxiv.org/pdf/{arxiv_id}" if arxiv_id else ""

    return ArxivPaper(
        arxiv_id=arxiv_id,
        title=title,
        summary=summary,
        authors=authors,
        published=_parse_published(pub_el.text if pub_el is not None else None),
        categories=categories,
        abs_url=abs_url,
        pdf_url=pdf_url,
    )


async def search(
    query: str,
    *,
    max_results: int = 10,
    sort_by: str = "relevance",
    timeout: float = 15.0,
    raise_on_rate_limit: bool = False,
) -> list[ArxivPaper]:
    """arxiv 검색 (search_query API). 키워드/쿼리 → ArxivPaper 목록.

    - sort_by: 'relevance' | 'lastUpdatedDate' | 'submittedDate'.
    - 빈 query / 네트워크 오류 / XML parse 실패 → 빈 리스트 (호출자 흐름 graceful).
    - raise_on_rate_limit=True 면 429 일 때 RateLimitError 를 던진다 (호출자가 '결과
      없음' 과 구분해 사용자에게 '잠시 후 재시도' 를 안내하도록). 기본 False(빈 리스트).
    - 기간·카테고리 필터는 여기서 안 한다 → filters.apply_filters 로 (순수 함수, 테스트 용이).
    """
    query = (query or "").strip()
    if not query:
        return []
    max_results = max(1, min(max_results, 50))
    if sort_by not in _SORT_VALUES:
        sort_by = "relevance"
    params = {
        "search_query": query,
        "start": "0",
        "max_results": str(max_results),
        "sortBy": sort_by,
        "sortOrder": "descending",
    }
    text_body: str | None = None
    try:
        async with httpx.AsyncClient(timeout=timeout, headers=_HEADERS) as client:
            # 429 면 Retry-After(없으면 3초) 만큼 기다렸다 1회 재시도 (arxiv 짧은 제한 흡수).
            for attempt in range(2):
                r = await client.get(_ARXIV_API, params=params)
                if r.status_code == 429:
                    if attempt == 0:
                        try:
                            wait = float(r.headers.get("Retry-After", "3"))
                        except (TypeError, ValueError):
                            wait = 3.0
                        await asyncio.sleep(min(max(wait, 1.0), 10.0))
                        continue
                    if raise_on_rate_limit:
                        raise RateLimitError("arxiv API rate limit (429)")
                    return []
                r.raise_for_status()
                text_body = r.text
                break
    except RateLimitError:
        raise
    except (httpx.HTTPError, httpx.TimeoutException) as e:
        logger.info("arxiv 검색 실패 (query=%r, %s) — 빈 결과", query, e)
        return []
    if text_body is None:
        return []

    try:
        root = ET.fromstring(text_body)
    except ET.ParseError as e:
        logger.warning("arxiv 검색 XML parse 실패 (query=%r): %s", query, e)
        return []

    out: list[ArxivPaper] = []
    for entry in root.findall("atom:entry", _ATOM_NS):
        paper = _parse_entry(entry)
        if paper is not None:
            out.append(paper)
    return out


async def search_keywords(
    keywords: list[str],
    *,
    max_results_per: int = 15,
    sort_by: str = "submittedDate",
    timeout: float = 15.0,
    delay: float = 3.0,
    raise_on_rate_limit: bool = False,
) -> list[ArxivPaper]:
    """키워드마다 **개별로** arxiv 검색한 뒤 결과를 합치고(union) 중복 제거한다.

    한 쿼리에 모든 키워드를 AND/OR 로 묶으면 (1) 쿼리가 너무 길어 arxiv 가 거부하고
    (2) AND 는 교집합이라 결과가 거의 없다. 그래서 "1번 키워드로 검색 → id 수집 → 2번
    키워드로 → … → 합쳐서 dedup = 전체 리스트" 방식으로 한다 (사용자 설계 2026-06-05).

    각 키워드는 build_query([kw]) 로 단어 AND(그 주제) 검색. 키워드 간은 합집합.
    arxiv rate limit(~1 req/3sec) 때문에 호출 사이 delay 초 대기한다.
    """
    from arxiv_harvester.filters import dedup_by_arxiv_id

    collected: list[ArxivPaper] = []
    first = True
    for kw in keywords:
        q = build_query([kw], match="OR")
        if not q:
            continue
        if not first and delay > 0:
            await asyncio.sleep(delay)
        first = False
        papers = await search(
            q,
            max_results=max_results_per,
            sort_by=sort_by,
            timeout=timeout,
            raise_on_rate_limit=raise_on_rate_limit,
        )
        collected.extend(papers)
    return dedup_by_arxiv_id(collected)


def build_query(keywords: list[str], *, match: str = "OR") -> str:
    """키워드 리스트 → arxiv search_query 문자열.

    **다단어 키워드는 정확 구문이 아니라 단어들의 AND** 로 묶는다. 예:
    "Learned Point Cloud Compression" → (all:Learned AND all:Point AND all:Cloud AND
    all:Compression) — 그 4단어를 모두 포함하는 논문(=그 주제)을 찾는다. 정확 구문
    (all:"...")은 그 4단어가 연속으로 나와야 해서 거의 매칭되지 않는다(검색 0 원인).

    키워드 간 결합: match='OR'(기본) 하나라도 / 'AND' 전부. 빈 키워드는 무시.
    """
    groups = []
    for kw in keywords:
        kw = (kw or "").strip()
        if not kw:
            continue
        words = [w for w in kw.split() if w]
        if not words:
            continue
        if len(words) == 1:
            groups.append(f"all:{words[0]}")
        else:
            inner = " AND ".join(f"all:{w}" for w in words)
            groups.append(f"({inner})")
    if not groups:
        return ""
    joiner = " AND " if match.upper() == "AND" else " OR "
    return joiner.join(groups)
