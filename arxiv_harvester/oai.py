"""arxiv_harvester.oai — arXiv OAI-PMH 메타 수확 (증분 갱신용).

`export.arxiv.org/oai2?verb=ListRecords&metadataPrefix=arXiv&from=<date>` 로 메타를
대량/증분 수확한다. **응답 schema 는 client.py 의 Atom 과 완전히 다르다** (namespace
`http://arxiv.org/OAI/arXiv/`) → 전용 파서. resumptionToken 페이징 + 503 Retry-After.

파서는 arxiv_papers 테이블 컬럼에 바로 쓸 dict 를 돌려준다 (title/abstract/authors/
categories/version/published/updated/doi/journal_ref). ArxivPaper(검색용 dataclass)와
필드가 달라(여기엔 updated/version 필요) dict 로 통일.

전체 backfill(from 없이)은 2.7M 레코드라 수일 — 전체는 Kaggle 이 담당하고, 여기선
`from=<watermark>` 증분 전용으로 쓴다.
"""
from __future__ import annotations

import asyncio
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, AsyncIterator

import httpx

logger = logging.getLogger("arxiv_harvester.oai")

# 2026: arxiv OAI 엔드포인트가 export.arxiv.org/oai2 → oaipmh.arxiv.org/oai 로 이전(301).
_OAI_URL = "https://oaipmh.arxiv.org/oai"
_OAI = "{http://www.openarchives.org/OAI/2.0/}"      # OAI-PMH envelope
_AX = "{http://arxiv.org/OAI/arXiv/}"                # arXiv metadata schema
_HEADERS = {
    "User-Agent": "arxiv-harvester/0.1 (+https://github.com/hyunkoome/arxiv-harvester)",
}


def _strip_version(arxiv_id: str) -> str:
    """'2106.09685v2' → '2106.09685' (base id 정규화 — DB PK dedup)."""
    if not arxiv_id:
        return arxiv_id
    i = arxiv_id.rfind("v")
    if i > 0 and arxiv_id[i + 1 :].isdigit():
        return arxiv_id[:i]
    return arxiv_id


def _txt(el: ET.Element | None, tag: str) -> str:
    if el is None:
        return ""
    c = el.find(tag)
    return (c.text or "").strip() if c is not None and c.text else ""


def _parse_date(s: str) -> datetime | None:
    """OAI 날짜('YYYY-MM-DD' 또는 ISO) → tz-aware datetime. 실패 None."""
    if not s:
        return None
    try:
        if len(s) == 10:  # YYYY-MM-DD
            return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_oai_record(record_el: ET.Element) -> dict[str, Any] | None:
    """OAI <record> → arxiv_papers 컬럼 dict. deleted/불완전 레코드는 None."""
    header = record_el.find(f"{_OAI}header")
    if header is not None and header.get("status") == "deleted":
        return None
    meta = record_el.find(f"{_OAI}metadata")
    if meta is None:
        return None
    ax = meta.find(f"{_AX}arXiv")
    if ax is None:
        return None

    arxiv_id = _strip_version(_txt(ax, f"{_AX}id"))
    title = _txt(ax, f"{_AX}title").replace("\n", " ").strip()
    if not arxiv_id or not title:
        return None

    abstract = _txt(ax, f"{_AX}abstract").replace("\n", " ").strip()
    categories = _txt(ax, f"{_AX}categories").split()
    created = _parse_date(_txt(ax, f"{_AX}created"))
    updated = _parse_date(_txt(ax, f"{_AX}updated")) or created

    authors: list[str] = []
    authors_el = ax.find(f"{_AX}authors")
    if authors_el is not None:
        for a in authors_el.findall(f"{_AX}author"):
            keyname = _txt(a, f"{_AX}keyname")
            forenames = _txt(a, f"{_AX}forenames")
            name = f"{forenames} {keyname}".strip()
            if name:
                authors.append(name)

    return {
        "arxiv_id": arxiv_id,
        "title": title,
        "abstract": abstract,
        "authors": authors,
        "categories": categories,
        "version": None,
        "published": created,
        "updated": updated,
        "doi": _txt(ax, f"{_AX}doi") or None,
        "journal_ref": _txt(ax, f"{_AX}journal-ref") or None,
        "source": "oai",
    }


def parse_list_records(xml_text: str) -> tuple[list[dict[str, Any]], str | None]:
    """ListRecords 응답 XML → (record dict 목록, resumptionToken | None).

    resumptionToken 이 비어 있거나 없으면 None (수확 종료 신호)."""
    root = ET.fromstring(xml_text)
    lr = root.find(f"{_OAI}ListRecords")
    if lr is None:
        return [], None
    records: list[dict[str, Any]] = []
    for rec in lr.findall(f"{_OAI}record"):
        parsed = parse_oai_record(rec)
        if parsed is not None:
            records.append(parsed)
    token_el = lr.find(f"{_OAI}resumptionToken")
    token = (token_el.text or "").strip() if token_el is not None and token_el.text else None
    return records, (token or None)


async def _fetch_with_retry(
    client: httpx.AsyncClient, params: dict[str, str], *, max_retries: int = 3,
) -> str:
    """OAI GET. 503(Retry-After) 면 그만큼 대기 후 재시도 (OAI 표준 backpressure)."""
    for attempt in range(max_retries + 1):
        r = await client.get(_OAI_URL, params=params)
        if r.status_code == 503 and attempt < max_retries:
            try:
                wait = float(r.headers.get("Retry-After", "20"))
            except (TypeError, ValueError):
                wait = 20.0
            logger.info("OAI 503 — %.0fs 후 재시도 (%d/%d)", wait, attempt + 1, max_retries)
            await asyncio.sleep(min(max(wait, 1.0), 120.0))
            continue
        r.raise_for_status()
        return r.text
    raise httpx.HTTPError("OAI 503 재시도 소진")


async def harvest_oai(
    from_date: str,
    *,
    until: str | None = None,
    set_spec: str | None = None,
    delay: float = 3.0,
    timeout: float = 60.0,
    max_pages: int | None = None,
) -> AsyncIterator[list[dict[str, Any]]]:
    """`from_date`('YYYY-MM-DD') 이후 갱신된 메타를 페이지별로 yield (async generator).

    set_spec: arxiv OAI set 으로 분야 한정 (예: 'cs', 'eess', 'math'). 미지정=전체 arxiv.
    resumptionToken 으로 페이징, 페이지 사이 delay 초 대기. max_pages 로 상한(테스트/소량).
    각 yield 는 arxiv_papers 컬럼 dict 리스트 — 호출자가 upsert.
    """
    params: dict[str, str] = {
        "verb": "ListRecords", "metadataPrefix": "arXiv", "from": from_date,
    }
    if until:
        params["until"] = until
    if set_spec:
        params["set"] = set_spec
    page = 0
    async with httpx.AsyncClient(
        timeout=timeout, headers=_HEADERS, follow_redirects=True,
    ) as client:
        while True:
            xml = await _fetch_with_retry(client, params)
            records, token = parse_list_records(xml)
            yield records
            page += 1
            if not token or (max_pages is not None and page >= max_pages):
                break
            # 이어받기는 resumptionToken 단독 (다른 파라미터 금지 — OAI 규약)
            params = {"verb": "ListRecords", "resumptionToken": token}
            await asyncio.sleep(delay)
