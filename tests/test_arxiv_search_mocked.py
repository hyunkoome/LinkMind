"""
backend.ingest.arxiv.search_arxiv 의 정적 흐름 검증 — arxiv API(Atom) 응답을
httpx mock 으로 가짜. 네트워크 없이 search_query 파싱·필드 추출만 확인.

확인:
- Atom feed → 논문 dict 목록 (arxiv_id / title / authors / abs_url / pdf_url)
- abs URL 의 http:// → https:// 정규화 + id 에서 pdf_url 합성
- 빈 query → 빈 리스트 (외부 호출 안 함)
- 네트워크 오류 → graceful 빈 리스트
"""

from __future__ import annotations

import httpx
import pytest

from backend.ingest import arxiv as arxiv_module


# arxiv API 가 반환하는 Atom feed 의 최소 형태 (entry 2개).
SAMPLE_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2106.09685v2</id>
    <title>LoRA: Low-Rank Adaptation of Large Language Models</title>
    <summary>We propose Low-Rank Adaptation, or LoRA, which freezes the
    pretrained model weights.</summary>
    <published>2021-06-17T17:37:18Z</published>
    <author><name>Edward J. Hu</name></author>
    <author><name>Yelong Shen</name></author>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2003.02014v1</id>
    <title>Redesigning SLAM for Arbitrary Multi-Camera Systems</title>
    <summary>Adding more cameras to SLAM systems improves robustness.</summary>
    <published>2020-03-04T12:00:00Z</published>
    <author><name>Juichung Kuo</name></author>
  </entry>
</feed>"""


class _FakeResp:
    def __init__(self, text: str):
        self.text = text

    def raise_for_status(self) -> None:
        pass


class _FakeClient:
    """httpx.AsyncClient 의 async context manager 형태만 흉내."""

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
async def test_search_arxiv_parses_entries(monkeypatch):
    monkeypatch.setattr(arxiv_module.httpx, "AsyncClient", _FakeClient)

    papers = await arxiv_module.search_arxiv("low rank adaptation", max_results=5)

    assert len(papers) == 2
    first = papers[0]
    assert first["arxiv_id"] == "2106.09685"
    assert first["title"].startswith("LoRA")
    assert first["authors"] == ["Edward J. Hu", "Yelong Shen"]
    # http:// → https:// 정규화
    assert first["abs_url"] == "https://arxiv.org/abs/2106.09685v2"
    # id 에서 pdf_url 합성
    assert first["pdf_url"] == "https://arxiv.org/pdf/2106.09685"
    assert papers[1]["arxiv_id"] == "2003.02014"


@pytest.mark.asyncio
async def test_search_arxiv_empty_query_skips_network(monkeypatch):
    # 네트워크를 타면 예외가 나도록 — 빈 query 면 호출 자체가 없어야 함.
    def _boom(*a, **k):
        raise AssertionError("빈 query 인데 네트워크를 탔다")

    monkeypatch.setattr(arxiv_module.httpx, "AsyncClient", _boom)
    assert await arxiv_module.search_arxiv("   ") == []


@pytest.mark.asyncio
async def test_search_arxiv_network_error_is_graceful(monkeypatch):
    def _factory(*a, **k):
        return _FakeClient(raise_exc=httpx.ConnectError("boom"))

    monkeypatch.setattr(arxiv_module.httpx, "AsyncClient", _factory)
    # agentic 흐름이 죽지 않도록 빈 리스트로 fallback.
    assert await arxiv_module.search_arxiv("transformers") == []
