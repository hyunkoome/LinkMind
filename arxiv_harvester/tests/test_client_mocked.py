"""arxiv_harvester.client.search — Atom 응답을 httpx mock 으로 가짜 (네트워크 없음).

확인: Atom feed → ArxivPaper 목록 (arxiv_id/title/authors/categories/abs_url/pdf_url),
http→https 정규화, pdf_url 합성, 빈 query skip, 네트워크 오류 graceful, build_query.
"""
from __future__ import annotations

import httpx
import pytest

from arxiv_harvester import build_query, search, search_keywords
from arxiv_harvester import client as client_module
from arxiv_harvester.models import ArxivPaper

SAMPLE_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2106.09685v2</id>
    <title>LoRA: Low-Rank Adaptation of Large Language Models</title>
    <summary>We propose Low-Rank Adaptation, or LoRA.</summary>
    <published>2021-06-17T17:37:18Z</published>
    <author><name>Edward J. Hu</name></author>
    <author><name>Yelong Shen</name></author>
    <category term="cs.CL"/>
    <category term="cs.AI"/>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2003.02014v1</id>
    <title>Redesigning SLAM for Arbitrary Multi-Camera Systems</title>
    <summary>Adding more cameras improves robustness.</summary>
    <published>2020-03-04T12:00:00Z</published>
    <author><name>Juichung Kuo</name></author>
    <category term="cs.RO"/>
  </entry>
</feed>"""


class _FakeResp:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        pass


class _FakeClient:
    def __init__(self, *args, body: str = SAMPLE_FEED, raise_exc: Exception | None = None, **kwargs):
        self._body = body
        self._raise = raise_exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None):
        if self._raise is not None:
            raise self._raise
        return _FakeResp(self._body)


@pytest.mark.asyncio
async def test_search_parses_entries(monkeypatch):
    monkeypatch.setattr(client_module.httpx, "AsyncClient", _FakeClient)

    papers = await search("low rank adaptation", max_results=5)

    assert len(papers) == 2
    first = papers[0]
    assert first.arxiv_id == "2106.09685"
    assert first.title.startswith("LoRA")
    assert first.authors == ["Edward J. Hu", "Yelong Shen"]
    assert first.categories == ["cs.CL", "cs.AI"]          # category term 파싱 (신규)
    assert first.abs_url == "https://arxiv.org/abs/2106.09685v2"  # http→https
    assert first.pdf_url == "https://arxiv.org/pdf/2106.09685"    # pdf 합성
    assert first.published is not None and first.published.year == 2021
    assert papers[1].arxiv_id == "2003.02014"


@pytest.mark.asyncio
async def test_search_empty_query_skips_network(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("빈 query 인데 네트워크를 탔다")

    monkeypatch.setattr(client_module.httpx, "AsyncClient", _boom)
    assert await search("   ") == []


@pytest.mark.asyncio
async def test_search_network_error_is_graceful(monkeypatch):
    def _factory(*a, **k):
        return _FakeClient(raise_exc=httpx.ConnectError("boom"))

    monkeypatch.setattr(client_module.httpx, "AsyncClient", _factory)
    assert await search("transformers") == []


def test_build_query_multiword_uses_and():
    # 다단어 키워드 = 단어 AND (정확 구문 아님 — 검색 0 방지)
    assert build_query(["Learned Point Cloud Compression"]) == (
        "(all:Learned AND all:Point AND all:Cloud AND all:Compression)"
    )
    # 단일 단어는 그대로
    assert build_query(["LightGaussian"]) == "all:LightGaussian"
    # 여러 키워드는 OR 결합, 각 다단어는 괄호 AND 그룹
    assert build_query(["gaussian splatting", "SLAM"]) == (
        "(all:gaussian AND all:splatting) OR all:SLAM"
    )
    assert build_query(["a", "b"], match="AND") == "all:a AND all:b"
    assert build_query([]) == ""
    assert build_query(["", "  "]) == ""


@pytest.mark.asyncio
async def test_search_keywords_unions_and_dedups(monkeypatch):
    # 키워드별로 다른 결과를 주고, 겹치는 논문은 dedup 되는지 (버전 무시)
    calls: list[str] = []

    async def _fake_search(query, *, max_results, sort_by, timeout, raise_on_rate_limit):
        calls.append(query)
        if "LightGaussian" in query:
            return [ArxivPaper(arxiv_id="2401.00001v1", title="LG")]
        if "Compact" in query:
            # 하나는 새 논문, 하나는 위와 같은 논문(버전 차이) → dedup
            return [
                ArxivPaper(arxiv_id="2402.00002", title="Compact"),
                ArxivPaper(arxiv_id="2401.00001v2", title="LG dup"),
            ]
        return []

    monkeypatch.setattr(client_module, "search", _fake_search)
    out = await search_keywords(
        ["LightGaussian", "Compact 3D Gaussian"], delay=0,
    )
    # 2회 호출(키워드당 1회), 각각 개별 쿼리
    assert len(calls) == 2
    # union 후 dedup → 2401.00001(첫 출현 유지) + 2402.00002
    assert [p.arxiv_id for p in out] == ["2401.00001v1", "2402.00002"]


@pytest.mark.asyncio
async def test_search_rate_limit_raises_when_opted_in(monkeypatch):
    class _RateResp:
        status_code = 429
        text = "Rate exceeded."
        headers: dict = {}

        def raise_for_status(self):
            raise httpx.HTTPStatusError("429", request=None, response=None)

    class _RateClient(_FakeClient):
        async def get(self, url, params=None):
            return _RateResp()

    # 재시도 sleep 즉시 통과 (테스트 빠르게)
    async def _no_sleep(_):
        return None

    monkeypatch.setattr(client_module.httpx, "AsyncClient", _RateClient)
    monkeypatch.setattr(client_module.asyncio, "sleep", _no_sleep)
    # 기본(graceful) — 재시도 후에도 429 → 빈 리스트
    assert await search("x") == []
    # opt-in — 재시도 후에도 429 → RateLimitError
    from arxiv_harvester import RateLimitError

    with pytest.raises(RateLimitError):
        await search("x", raise_on_rate_limit=True)
