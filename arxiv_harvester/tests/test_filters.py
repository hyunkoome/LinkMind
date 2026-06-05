"""arxiv_harvester.filters 순수 함수 테스트 (네트워크 없음)."""
from __future__ import annotations

from datetime import datetime, timezone

from arxiv_harvester import (
    apply_filters,
    dedup_by_arxiv_id,
    filter_by_category,
    filter_by_date,
)
from arxiv_harvester.models import ArxivPaper


def _p(arxiv_id, *, published=None, categories=None) -> ArxivPaper:
    return ArxivPaper(
        arxiv_id=arxiv_id,
        title=f"paper {arxiv_id}",
        published=published,
        categories=categories or [],
    )


def test_dedup_ignores_version_keeps_order():
    papers = [_p("2106.09685v2"), _p("2003.02014v1"), _p("2106.09685v1"), _p("2003.02014")]
    out = dedup_by_arxiv_id(papers)
    assert [p.arxiv_id for p in out] == ["2106.09685v2", "2003.02014v1"]


def test_dedup_skips_empty_id():
    out = dedup_by_arxiv_id([_p(""), _p("2003.02014")])
    assert [p.arxiv_id for p in out] == ["2003.02014"]


def test_filter_by_date_inclusive_bounds():
    d = datetime(2021, 6, 17, tzinfo=timezone.utc)
    papers = [
        _p("a", published=datetime(2020, 1, 1, tzinfo=timezone.utc)),
        _p("b", published=d),
        _p("c", published=datetime(2022, 1, 1, tzinfo=timezone.utc)),
    ]
    out = filter_by_date(papers, date_from=d)
    assert {p.arxiv_id for p in out} == {"b", "c"}
    out = filter_by_date(papers, date_to=d)
    assert {p.arxiv_id for p in out} == {"a", "b"}


def test_filter_by_date_no_bounds_passthrough():
    papers = [_p("a"), _p("b", published=datetime(2021, 1, 1, tzinfo=timezone.utc))]
    # 경계 미지정 → published 없는 것도 그대로 통과
    assert len(filter_by_date(papers)) == 2


def test_filter_by_date_excludes_undated_when_bounded():
    papers = [_p("a"), _p("b", published=datetime(2021, 1, 1, tzinfo=timezone.utc))]
    out = filter_by_date(papers, date_from=datetime(2020, 1, 1, tzinfo=timezone.utc))
    assert [p.arxiv_id for p in out] == ["b"]


def test_filter_by_category_case_insensitive():
    papers = [_p("a", categories=["cs.CV"]), _p("b", categories=["cs.RO"]), _p("c", categories=[])]
    out = filter_by_category(papers, ["cs.cv"])
    assert [p.arxiv_id for p in out] == ["a"]


def test_filter_by_category_empty_passthrough():
    papers = [_p("a", categories=["cs.CV"]), _p("b")]
    assert len(filter_by_category(papers, None)) == 2
    assert len(filter_by_category(papers, [])) == 2


def test_apply_filters_pipeline():
    d = datetime(2021, 1, 1, tzinfo=timezone.utc)
    papers = [
        _p("2106.09685v2", published=datetime(2021, 6, 1, tzinfo=timezone.utc), categories=["cs.CL"]),
        _p("2106.09685v1", published=datetime(2021, 6, 1, tzinfo=timezone.utc), categories=["cs.CL"]),
        _p("1999.00001", published=datetime(2019, 1, 1, tzinfo=timezone.utc), categories=["cs.CL"]),
        _p("2200.00002", published=datetime(2022, 1, 1, tzinfo=timezone.utc), categories=["math.AG"]),
    ]
    out = apply_filters(papers, date_from=d, categories=["cs.CL"], dedup=True)
    # 1999(기간밖) 제외, math(카테고리밖) 제외, v2/v1 중복 → 1개
    assert [p.arxiv_id for p in out] == ["2106.09685v2"]
