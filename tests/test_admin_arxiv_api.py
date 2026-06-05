"""backend.api.admin_arxiv 단위 테스트 (cpu, mock).

엔드포인트 함수를 직접 호출(FastAPI 안 띄움)하고 harvester/ingest/repository 를
monkeypatch. 검증: 키워드 CRUD, arxiv 검색 미리보기(build_query 적용), collect 가
arxiv_id 별로 pdf ingest 를 호출 + 잘못된 id 거부(SSRF 방지), 모든 라우트가
require_space_admin 으로 게이팅.
"""
from __future__ import annotations

import inspect
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import BackgroundTasks, HTTPException

from arxiv_harvester.models import ArxivPaper
from backend.api import admin_arxiv
from backend.api.admin_arxiv import (
    ArxivSearchRequest,
    CollectRequest,
    KeywordCreate,
    KeywordToggle,
)
from backend.api.deps import require_space_admin


def _admin() -> dict:
    return {"id": uuid.uuid4(), "email": "a@b.c"}


def _session():
    s = SimpleNamespace()
    s.commit = AsyncMock()
    return s


# ── 게이팅 ──────────────────────────────────────────────────────────────

def test_all_routes_gated_by_space_admin():
    """모든 admin_arxiv 라우트가 require_space_admin Depends 를 가져야 (member 차단)."""
    for route in admin_arxiv.router.routes:
        sig = inspect.signature(route.endpoint)
        deps = [
            p.default.dependency
            for p in sig.parameters.values()
            if hasattr(p.default, "dependency")
        ]
        assert require_space_admin in deps, f"{route.endpoint.__name__} 미게이팅"


# ── 검색 미리보기 ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_preview_keywords_local_fts_union(monkeypatch):
    captured = {}

    async def _search(session, *, keywords, date_from, date_to, categories, limit):
        captured["keywords"] = keywords
        return [{
            "arxiv_id": "2106.09685", "title": "LoRA", "abstract": "x",
            "authors": ["A B"], "categories": ["cs.CL"], "published": None, "version": "v1",
        }]

    monkeypatch.setattr(admin_arxiv.repository, "search_arxiv_papers", _search)
    req = ArxivSearchRequest(keywords=["gaussian splatting", "SLAM"], max_results=10)
    out = await admin_arxiv.search_arxiv_preview(req, _admin=_admin(), session=_session())

    assert out["count"] == 1
    assert out["papers"][0]["arxiv_id"] == "2106.09685"
    # 로컬 arxiv_papers FTS union — pdf_url 합성
    assert out["papers"][0]["pdf_url"] == "https://arxiv.org/pdf/2106.09685"
    assert captured["keywords"] == ["gaussian splatting", "SLAM"]


@pytest.mark.asyncio
async def test_search_preview_empty_returns_nothing():
    out = await admin_arxiv.search_arxiv_preview(
        ArxivSearchRequest(), _admin=_admin(), session=_session(),
    )
    assert out == {"papers": [], "count": 0}


# ── collect (수집 → pdf ingest) ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_collect_calls_ingest_per_id(monkeypatch):
    calls: list[str] = []

    async def _ingest(payload, background):
        calls.append(payload.url)
        return SimpleNamespace(item_id="item", created=True, title="T")

    monkeypatch.setattr(admin_arxiv, "ingest_pdf_endpoint", _ingest)
    req = CollectRequest(arxiv_ids=["2106.09685", "2003.02014"])
    out = await admin_arxiv.collect_arxiv(req, BackgroundTasks(), _admin=_admin())

    assert out["collected"] == 2
    assert calls == [
        "https://arxiv.org/pdf/2106.09685",
        "https://arxiv.org/pdf/2003.02014",
    ]


@pytest.mark.asyncio
async def test_collect_rejects_non_arxiv_id(monkeypatch):
    async def _ingest(payload, background):
        raise AssertionError("잘못된 id 인데 ingest 가 호출됨 (SSRF 방어 실패)")

    monkeypatch.setattr(admin_arxiv, "ingest_pdf_endpoint", _ingest)
    req = CollectRequest(arxiv_ids=["http://evil.com/x.pdf", "'; DROP TABLE--"])
    out = await admin_arxiv.collect_arxiv(req, BackgroundTasks(), _admin=_admin())

    assert out["collected"] == 0
    assert all(not r["ok"] for r in out["results"])


@pytest.mark.asyncio
async def test_collect_continues_on_one_failure(monkeypatch):
    async def _ingest(payload, background):
        if "2003.02014" in payload.url:
            raise RuntimeError("ingest 폭발")
        return SimpleNamespace(item_id="ok", created=True, title="T")

    monkeypatch.setattr(admin_arxiv, "ingest_pdf_endpoint", _ingest)
    req = CollectRequest(arxiv_ids=["2106.09685", "2003.02014"])
    out = await admin_arxiv.collect_arxiv(req, BackgroundTasks(), _admin=_admin())

    assert out["collected"] == 1  # 하나 실패해도 나머지 진행
    assert out["total"] == 2


# ── 키워드 CRUD ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_add_keyword(monkeypatch):
    async def _add(session, *, space_id, user_id, keyword):
        return {"id": uuid.uuid4(), "keyword": keyword, "enabled": True}

    monkeypatch.setattr(admin_arxiv.repository, "add_collection_keyword", _add)
    out = await admin_arxiv.add_keyword(
        KeywordCreate(keyword="SLAM"), admin=_admin(),
        space_id=uuid.uuid4(), session=_session(),
    )
    assert out["created"] is True and out["keyword"] == "SLAM"


@pytest.mark.asyncio
async def test_add_keyword_duplicate_is_idempotent(monkeypatch):
    async def _add(session, *, space_id, user_id, keyword):
        return None  # ON CONFLICT DO NOTHING

    monkeypatch.setattr(admin_arxiv.repository, "add_collection_keyword", _add)
    out = await admin_arxiv.add_keyword(
        KeywordCreate(keyword="SLAM"), admin=_admin(),
        space_id=uuid.uuid4(), session=_session(),
    )
    assert out["created"] is False


@pytest.mark.asyncio
async def test_delete_keyword_404_when_missing(monkeypatch):
    async def _del(session, *, space_id, keyword_id):
        return False

    monkeypatch.setattr(admin_arxiv.repository, "delete_collection_keyword", _del)
    with pytest.raises(HTTPException) as e:
        await admin_arxiv.delete_keyword(
            uuid.uuid4(), _admin=_admin(), space_id=uuid.uuid4(), session=_session(),
        )
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_toggle_keyword(monkeypatch):
    async def _set(session, *, space_id, keyword_id, enabled):
        return True

    monkeypatch.setattr(admin_arxiv.repository, "set_collection_keyword_enabled", _set)
    out = await admin_arxiv.toggle_keyword(
        uuid.uuid4(), KeywordToggle(enabled=False),
        _admin=_admin(), space_id=uuid.uuid4(), session=_session(),
    )
    assert out["enabled"] is False
