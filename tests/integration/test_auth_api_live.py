"""
인증 API e2e 테스트 — backend 가 떠 있을 때만 (멀티테넌트, 2026-06-03).

운영 모델: self-signup 없음. 첫 관리자는 bootstrap(user 0명일 때), 멤버는 루트가 발급.
CI 에선 자동 skip (integration marker + ci.yml 이 tests/integration 디렉토리 제외).

관리자 자격이 필요한 테스트는 env 로 받는다 (bootstrap 모델이라 고정 seed 계정이 없음):
    LINKMIND_TEST_ADMIN_EMAIL / LINKMIND_TEST_ADMIN_PASSWORD
미설정이거나 미초기화(bootstrap 필요) 인스턴스면 해당 테스트는 skip (사용자 bootstrap 흐름 보호).

    pytest -m integration tests/integration/test_auth_api_live.py
"""

from __future__ import annotations

import os

import httpx
import pytest

API = os.getenv("LINKMIND_API_BASE", "http://localhost:8000")
ADMIN_EMAIL = os.getenv("LINKMIND_TEST_ADMIN_EMAIL", "")
ADMIN_PW = os.getenv("LINKMIND_TEST_ADMIN_PASSWORD", "")


@pytest.fixture(scope="module")
def base() -> str:
    try:
        r = httpx.get(f"{API}/health", timeout=2.0)
        if r.status_code != 200:
            pytest.skip(f"backend health 비정상 ({r.status_code})")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"backend 미가동 ({API}): {e}")
    return API


@pytest.fixture(scope="module")
def admin_client(base: str):
    """관리자 세션. 테스트는 절대 bootstrap 하지 않는다(사용자 첫 조직 흐름 보호) —
    미초기화면 skip, 초기화됐는데 env 자격 없으면 skip."""
    c = httpx.Client(base_url=base, timeout=10.0)
    needed = c.get("/auth/bootstrap-needed").json()["needed"]
    if needed:
        c.close()
        pytest.skip("미초기화 인스턴스 — 브라우저에서 첫 조직(bootstrap)을 먼저 만드세요")
    if not (ADMIN_EMAIL and ADMIN_PW):
        c.close()
        pytest.skip("LINKMIND_TEST_ADMIN_EMAIL/PASSWORD 미설정 — 관리자 자격 필요")
    r = c.post("/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PW})
    if r.status_code != 200:
        c.close()
        pytest.skip("LINKMIND_TEST_ADMIN 자격으로 로그인 실패")
    yield c
    c.close()


# ── 인증 불필요 ────────────────────────────────────────────────

@pytest.mark.integration
def test_protected_route_401_without_cookie(base: str):
    with httpx.Client(base_url=base, timeout=10.0) as c:
        assert c.get("/wiki/_meta/stats").status_code == 401


@pytest.mark.integration
def test_admin_endpoint_requires_auth(base: str):
    with httpx.Client(base_url=base, timeout=10.0) as c:
        r = c.post(
            "/auth/admin/users",
            json={"email": "x@linkmind.local", "password": "123456"},
        )
        assert r.status_code == 401


@pytest.mark.integration
def test_bootstrap_state_consistent(base: str):
    # 초기화된 인스턴스면 bootstrap POST 는 403. 미초기화면 needed=true (skip).
    with httpx.Client(base_url=base, timeout=10.0) as c:
        needed = c.get("/auth/bootstrap-needed").json()["needed"]
        if needed:
            pytest.skip("미초기화 — bootstrap 가능 상태")
        r = c.post(
            "/auth/bootstrap",
            json={"org_name": "X", "email": "x@linkmind.local", "password": "123456"},
        )
        assert r.status_code == 403


# ── 관리자 세션 필요 ───────────────────────────────────────────

@pytest.mark.integration
def test_admin_can_access_and_me(admin_client: httpx.Client):
    assert admin_client.get("/wiki/_meta/stats").status_code == 200
    me = admin_client.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == ADMIN_EMAIL


@pytest.mark.integration
def test_admin_create_member_joins_same_org_space(admin_client: httpx.Client, base: str):
    member_email = "pytest-member@linkmind.local"
    member_pw = "member-pw-123"
    admin_space = admin_client.get("/auth/me").json()["active_space_id"]
    r = admin_client.post(
        "/auth/admin/users",
        json={"email": member_email, "password": member_pw, "display_name": "PM"},
    )
    assert r.status_code in (201, 409), r.text
    # 발급된 멤버로 로그인 → 관리자와 같은 조직 space (데이터 공유).
    with httpx.Client(base_url=base, timeout=10.0) as mc:
        mr = mc.post("/auth/login", json={"email": member_email, "password": member_pw})
        assert mr.status_code == 200
        assert mr.json()["active_space_id"] == admin_space


@pytest.mark.integration
def test_admin_create_short_password_422(admin_client: httpx.Client):
    r = admin_client.post(
        "/auth/admin/users",
        json={"email": "x@linkmind.local", "password": "123"},
    )
    assert r.status_code == 422


@pytest.mark.integration
def test_login_wrong_password_401(admin_client: httpx.Client, base: str):
    with httpx.Client(base_url=base, timeout=10.0) as c:
        r = c.post("/auth/login", json={"email": ADMIN_EMAIL, "password": "definitely-wrong"})
        assert r.status_code == 401
