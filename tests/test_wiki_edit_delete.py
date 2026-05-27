"""
PATCH /wiki/{slug} + DELETE /wiki/{slug} 의 schema / 라우팅 cpu unit test.

real DB / Qdrant 검증은 tests/integration/test_wiki_edit_delete_live.py (별도, backend 띄워야 도는). 여기는 backend 안 띄워도 도는 가벼운 검증만.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import APIRouter

from backend.api import wiki as wiki_api
from backend.schemas.models import (
    WikiPageDeleteResponse,
    WikiPageEditRequest,
)


def test_wiki_page_edit_request_all_optional():
    """모든 필드 optional — 셋 다 None 으로도 instantiate. 400 던지는 책임은 endpoint."""
    req = WikiPageEditRequest()
    assert req.title is None
    assert req.description is None
    assert req.body is None


def test_wiki_page_edit_request_partial():
    """단일 필드만 제공 가능 — 변경 안 할 필드는 None 로 와도 backend COALESCE 가 보존."""
    req = WikiPageEditRequest(title="새 제목")
    assert req.title == "새 제목"
    assert req.description is None
    assert req.body is None

    req2 = WikiPageEditRequest(body="## 본문\n수정\n")
    assert req2.body == "## 본문\n수정\n"
    assert req2.title is None


def test_wiki_page_edit_request_empty_string_allowed():
    """빈 문자열은 None 과 구분 — description 을 명시적으로 비우고 싶을 수 있음."""
    req = WikiPageEditRequest(description="")
    assert req.description == ""           # None 아님


def test_wiki_page_delete_response_required_fields():
    """삭제 응답 — 모든 필드 필수 (default 없음)."""
    page_id = uuid4()
    resp = WikiPageDeleteResponse(
        deleted_wiki_slug="github__foo-bar",
        deleted_wiki_page_id=page_id,
        deleted_items_count=3,
        affected_other_wikis_count=1,
        qdrant_wiki_status=0,
        qdrant_items_status_sum=0,
    )
    assert resp.deleted_wiki_slug == "github__foo-bar"
    assert resp.deleted_wiki_page_id == page_id
    assert resp.deleted_items_count == 3
    assert resp.affected_other_wikis_count == 1


def test_wiki_page_delete_response_no_items_case():
    """연결 items 0 도 정상 — 옛 빈 wiki page 정리 케이스."""
    resp = WikiPageDeleteResponse(
        deleted_wiki_slug="orphan__zzz",
        deleted_wiki_page_id=uuid4(),
        deleted_items_count=0,
        affected_other_wikis_count=0,
        qdrant_wiki_status=0,
        qdrant_items_status_sum=0,
    )
    assert resp.deleted_items_count == 0


def test_wiki_router_has_patch_and_delete_endpoints():
    """라우터에 PATCH /{slug} + DELETE /{slug} 가 모두 등록됐는지 확인.

    같은 path 라도 HTTP method 가 다르면 별 route. FastAPI 가 잘 분리하는지 회귀 방지.
    """
    router: APIRouter = wiki_api.router
    paths_methods: set[tuple[str, str]] = set()
    for route in router.routes:
        # APIRoute 는 path + methods 둘 다 갖춤
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None) or set()
        if path is None:
            continue
        for m in methods:
            paths_methods.add((path, m))

    assert ("/{slug}", "GET") in paths_methods, "GET /{slug} (조회) 누락"
    assert ("/{slug}", "PATCH") in paths_methods, "PATCH /{slug} (수동 편집) 누락"
    assert ("/{slug}", "DELETE") in paths_methods, "DELETE /{slug} (영구 삭제) 누락"


def test_wiki_router_response_models_correct():
    """PATCH 는 WikiPageDetail / DELETE 는 WikiPageDeleteResponse 반환 — schema 회귀 방지."""
    from backend.schemas.models import WikiPageDetail

    router: APIRouter = wiki_api.router
    patch_route = next(
        r for r in router.routes
        if getattr(r, "path", None) == "/{slug}"
        and "PATCH" in (getattr(r, "methods", None) or set())
    )
    delete_route = next(
        r for r in router.routes
        if getattr(r, "path", None) == "/{slug}"
        and "DELETE" in (getattr(r, "methods", None) or set())
    )

    assert patch_route.response_model is WikiPageDetail
    assert delete_route.response_model is WikiPageDeleteResponse


@pytest.mark.parametrize(
    "payload,changed",
    [
        ({}, []),    # 셋 다 None → endpoint 가 400 (별 case)
        ({"title": "T"}, ["title"]),
        ({"description": "D"}, ["description"]),
        ({"body": "B"}, ["body"]),
        ({"title": "T", "body": "B"}, ["title", "body"]),
        ({"title": "T", "description": "D", "body": "B"}, ["title", "description", "body"]),
    ],
)
def test_wiki_page_edit_request_combinations(payload, changed):
    """모든 조합 — Pydantic 이 잘 parse 하는지."""
    req = WikiPageEditRequest(**payload)
    for field in ["title", "description", "body"]:
        if field in changed:
            assert getattr(req, field) == payload[field]
        else:
            assert getattr(req, field) is None
