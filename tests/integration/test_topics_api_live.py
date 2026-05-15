"""
LinkMind backend 가 떠 있을 때만 도는 e2e 테스트 — Topics API 의 실제 응답 검증.

CI (GitHub Actions) 에선 자동 skip — pytest.ini 의 addopts 가 'integration'
marker 를 deselect. 로컬에서 backend (`bash scripts/step5_run_dev.sh`) 띄운 후:

    pytest -m integration tests/integration/

또는 모든 (default + integration) 를 한 번에:

    pytest -m '' tests/

이 테스트는 schema migration / API contract 회귀 방지가 목적 — 응답 형태/필드만
확인하고 실제 데이터 내용은 검증하지 않음 (사용자 DB 상태에 의존하지 않도록).
"""

from __future__ import annotations

import os

import httpx
import pytest


API = os.getenv("LINKMIND_API_BASE", "http://localhost:8000")


@pytest.fixture(scope="module")
def live_client() -> httpx.Client:
    try:
        r = httpx.get(f"{API}/health", timeout=2.0)
        if r.status_code != 200:
            pytest.skip(f"backend health 비정상 ({r.status_code}) — backend 띄우고 재시도")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"backend 미가동 ({API}): {e}")
    with httpx.Client(base_url=API, timeout=10.0) as c:
        yield c


@pytest.mark.integration
def test_health_ok(live_client: httpx.Client):
    r = live_client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "postgres" in body["components"]


@pytest.mark.integration
def test_topics_list_shape(live_client: httpx.Client):
    """GET /topics 응답이 list[TopicSummary] 형태 — 데이터 유무 무관."""
    r = live_client.get("/topics", params={"limit": 5})
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    for t in body:
        # API contract — TopicSummary 의 필수 필드
        for key in ("id", "slug", "title", "tags", "item_count"):
            assert key in t, f"필수 키 '{key}' 누락: {t}"
        assert isinstance(t["tags"], list)
        assert isinstance(t["item_count"], int)


@pytest.mark.integration
def test_topics_detail_404_for_unknown_slug(live_client: httpx.Client):
    r = live_client.get("/topics/nonexistent:00000000000")
    assert r.status_code == 404


@pytest.mark.integration
def test_topic_detail_shape_when_present(live_client: httpx.Client):
    """topic 이 하나라도 있으면 첫 번째에 대해 GET /topics/{slug} 검증.

    slug 안에 '/' 가 있으면 (예: github:owner/repo) path split 되니 UUID id 로 호출.
    """
    listed = live_client.get("/topics", params={"limit": 1}).json()
    if not listed:
        pytest.skip("DB 에 topic 없음 — backfill_external_ids 먼저 실행")
    topic_id = listed[0]["id"]
    r = live_client.get(f"/topics/{topic_id}")
    assert r.status_code == 200
    body = r.json()
    for key in ("id", "slug", "title", "items"):
        assert key in body
    assert isinstance(body["items"], list)
    for it in body["items"]:
        for k in ("id", "source_type", "role", "confidence", "source"):
            assert k in it
