"""
인증 API e2e 테스트 — backend 가 떠 있을 때만 (멀티테넌트 단계 A, 2026-06-03).

CI 에선 자동 skip (integration marker + ci.yml 이 tests/integration 디렉토리 제외).
로컬에서 backend (`bash scripts/step5_run_dev.sh`) + seed 계정으로:

    pytest -m integration tests/integration/test_auth_api_live.py

검증: 무쿠키 보호라우터 401 → 로그인 → 쿠키로 보호라우터 200 → /auth/me → logout.
seed 계정(LINKMIND_SEED_USER_EMAIL/PASSWORD)에 의존.
"""

from __future__ import annotations

import os

import httpx
import pytest

API = os.getenv("LINKMIND_API_BASE", "http://localhost:8000")
SEED_EMAIL = os.getenv("LINKMIND_SEED_USER_EMAIL", "admin@linkmind.local")
SEED_PASSWORD = os.getenv("LINKMIND_SEED_USER_PASSWORD", "linkmind")


@pytest.fixture(scope="module")
def base() -> str:
    try:
        r = httpx.get(f"{API}/health", timeout=2.0)
        if r.status_code != 200:
            pytest.skip(f"backend health 비정상 ({r.status_code})")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"backend 미가동 ({API}): {e}")
    return API


@pytest.mark.integration
def test_protected_route_401_without_cookie(base: str):
    # 쿠키 없이 보호 라우터 → 401.
    with httpx.Client(base_url=base, timeout=10.0) as c:
        r = c.get("/wiki/_meta/stats")
        assert r.status_code == 401


@pytest.mark.integration
def test_login_sets_cookie_and_grants_access(base: str):
    with httpx.Client(base_url=base, timeout=10.0) as c:
        # 로그인 → Set-Cookie.
        r = c.post("/auth/login", json={"email": SEED_EMAIL, "password": SEED_PASSWORD})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["email"] == SEED_EMAIL
        assert "active_space_id" in body and body["spaces"]
        # httpx.Client 가 쿠키를 보관 → 이후 보호 라우터 200.
        r2 = c.get("/wiki/_meta/stats")
        assert r2.status_code == 200
        # /auth/me 로 현재 사용자 확인.
        me = c.get("/auth/me")
        assert me.status_code == 200
        assert me.json()["email"] == SEED_EMAIL


@pytest.mark.integration
def test_admin_create_member_joins_same_org_space(base: str):
    # 루트 관리자가 멤버 발급 → 그 멤버는 *관리자와 같은 조직 space* 에 합류(데이터 공유).
    member_email = "pytest-member@linkmind.local"
    member_pw = "member-pw-123"
    with httpx.Client(base_url=base, timeout=10.0) as c:
        lr = c.post("/auth/login", json={"email": SEED_EMAIL, "password": SEED_PASSWORD})
        assert lr.status_code == 200
        admin_space = lr.json()["active_space_id"]
        # 멱등 — 이미 있으면 409.
        r = c.post(
            "/auth/admin/users",
            json={"email": member_email, "password": member_pw, "display_name": "PM"},
        )
        assert r.status_code in (201, 409), r.text
        # 발급된 멤버로 로그인 → 관리자와 같은 조직 space.
        mr = c.post("/auth/login", json={"email": member_email, "password": member_pw})
        assert mr.status_code == 200
        body = mr.json()
        assert body["active_space_id"] == admin_space, "멤버가 조직 space 를 공유해야 함"


@pytest.mark.integration
def test_bootstrap_blocked_when_initialized(base: str):
    # seed/기존 계정이 있는 인스턴스 → bootstrap 비활성(needed=false) + POST 403.
    with httpx.Client(base_url=base, timeout=10.0) as c:
        n = c.get("/auth/bootstrap-needed")
        assert n.status_code == 200
        assert n.json()["needed"] is False
        r = c.post(
            "/auth/bootstrap",
            json={"org_name": "X", "email": "x@linkmind.local", "password": "123456"},
        )
        assert r.status_code == 403


@pytest.mark.integration
def test_admin_endpoint_requires_auth(base: str):
    # 무인증 admin API → 401 (인증 자체 없음).
    with httpx.Client(base_url=base, timeout=10.0) as c:
        r = c.post(
            "/auth/admin/users",
            json={"email": "x@linkmind.local", "password": "123456"},
        )
        assert r.status_code == 401


@pytest.mark.integration
def test_admin_create_short_password_422(base: str):
    with httpx.Client(base_url=base, timeout=10.0) as c:
        c.post("/auth/login", json={"email": SEED_EMAIL, "password": SEED_PASSWORD})
        r = c.post(
            "/auth/admin/users",
            json={"email": "x@linkmind.local", "password": "123"},
        )
        assert r.status_code == 422


@pytest.mark.integration
def test_login_wrong_password_401(base: str):
    with httpx.Client(base_url=base, timeout=10.0) as c:
        r = c.post("/auth/login", json={"email": SEED_EMAIL, "password": "definitely-wrong"})
        assert r.status_code == 401


@pytest.mark.integration
def test_logout_clears_access(base: str):
    with httpx.Client(base_url=base, timeout=10.0) as c:
        c.post("/auth/login", json={"email": SEED_EMAIL, "password": SEED_PASSWORD})
        assert c.get("/auth/me").status_code == 200
        c.post("/auth/logout")
        # 쿠키 삭제 후 보호 라우터 401.
        assert c.get("/auth/me").status_code == 401
