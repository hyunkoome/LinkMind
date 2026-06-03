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
