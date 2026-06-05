"""arxiv_harvester.filters — ArxivPaper 목록의 순수 필터 (네트워크 없음).

검색 결과를 기간/카테고리로 거르고 중복(같은 논문의 버전 차이)을 제거한다. 순수 함수라
단위 테스트가 쉽다. 기간/카테고리를 arxiv API search_query 로 푸시다운하지 않고 여기서
거르는 이유: arxiv 의 date-range 문법이 까다로워 회귀 위험이 크고, 클라이언트측 필터가
명확·검증가능하기 때문.
"""
from __future__ import annotations

from datetime import datetime

from arxiv_harvester.models import ArxivPaper


def _strip_version(arxiv_id: str) -> str:
    """'2106.09685v2' → '2106.09685' (버전 suffix 제거). 같은 논문 판별용."""
    if not arxiv_id:
        return arxiv_id
    base = arxiv_id
    # 끝의 v<digits> 제거
    i = base.rfind("v")
    if i > 0 and base[i + 1 :].isdigit():
        return base[:i]
    return base


def dedup_by_arxiv_id(papers: list[ArxivPaper]) -> list[ArxivPaper]:
    """같은 논문(버전 무시)을 제거. 첫 출현 유지, 순서 보존."""
    seen: set[str] = set()
    out: list[ArxivPaper] = []
    for p in papers:
        key = _strip_version(p.arxiv_id)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def filter_by_date(
    papers: list[ArxivPaper],
    *,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> list[ArxivPaper]:
    """published 가 [date_from, date_to] 안인 논문만. 경계는 포함. published 없으면 제외
    (필터가 지정된 경우에만 — 둘 다 None 이면 그대로 통과)."""
    if date_from is None and date_to is None:
        return list(papers)
    out: list[ArxivPaper] = []
    for p in papers:
        if p.published is None:
            continue
        if date_from is not None and p.published < date_from:
            continue
        if date_to is not None and p.published > date_to:
            continue
        out.append(p)
    return out


def filter_by_category(
    papers: list[ArxivPaper], categories: list[str] | None,
) -> list[ArxivPaper]:
    """논문 categories 중 하나라도 지정 categories 에 들면 통과. 빈/None 이면 그대로."""
    if not categories:
        return list(papers)
    wanted = {c.strip().lower() for c in categories if c.strip()}
    if not wanted:
        return list(papers)
    return [
        p for p in papers
        if any(c.lower() in wanted for c in p.categories)
    ]


def apply_filters(
    papers: list[ArxivPaper],
    *,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    categories: list[str] | None = None,
    dedup: bool = True,
) -> list[ArxivPaper]:
    """기간 → 카테고리 → dedup 순으로 적용한 결과. 모든 인자 생략 시 dedup 만(기본)."""
    out = filter_by_date(papers, date_from=date_from, date_to=date_to)
    out = filter_by_category(out, categories)
    if dedup:
        out = dedup_by_arxiv_id(out)
    return out
