"""arxiv_harvester — 키워드 기반 arxiv 논문 수집 (검색 + 필터).

LinkMind 비의존 독립 패키지 (나중에 별도 OSS repo + PyPI 로 추출 예정, MIT).
LinkMind 는 이 패키지를 in-process import 로 사용한다.

빠른 사용:
    import asyncio
    from arxiv_harvester import search, apply_filters, build_query

    async def main():
        papers = await search(build_query(["gaussian splatting", "SLAM"]), max_results=20)
        papers = apply_filters(papers, categories=["cs.CV"], dedup=True)
        for p in papers:
            print(p.arxiv_id, p.title)

    asyncio.run(main())
"""
from __future__ import annotations

from arxiv_harvester.client import (
    RateLimitError,
    build_query,
    parse_arxiv_id,
    search,
    search_keywords,
)
from arxiv_harvester.filters import (
    apply_filters,
    dedup_by_arxiv_id,
    filter_by_category,
    filter_by_date,
)
from arxiv_harvester.keywords import load_keywords
from arxiv_harvester.models import ArxivPaper

__all__ = [
    "ArxivPaper",
    "RateLimitError",
    "search",
    "search_keywords",
    "build_query",
    "parse_arxiv_id",
    "apply_filters",
    "filter_by_date",
    "filter_by_category",
    "dedup_by_arxiv_id",
    "load_keywords",
]

__version__ = "0.1.0"
