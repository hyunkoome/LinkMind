"""arxiv_harvester.models — 검색 결과 논문의 순수 데이터 모델.

LinkMind 등 외부 의존 없음 (독립 OSS 패키지). dataclass 만으로 표현한다.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class ArxivPaper:
    """arxiv 한 편의 메타데이터 (검색·필터 단계의 값 객체).

    published 는 datetime 으로 파싱해 기간 필터(filters.filter_by_date)가 비교에 바로
    쓸 수 있게 한다. 파싱 실패 시 None.
    """

    arxiv_id: str
    title: str
    summary: str = ""
    authors: list[str] = field(default_factory=list)
    published: datetime | None = None
    categories: list[str] = field(default_factory=list)
    abs_url: str = ""
    pdf_url: str = ""

    def to_dict(self) -> dict:
        """JSON 직렬화용 dict (published 는 ISO 문자열). REST/HTTP 응답·로깅에 사용."""
        d = asdict(self)
        d["published"] = self.published.isoformat() if self.published else None
        return d
