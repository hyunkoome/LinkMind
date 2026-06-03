"""
인증 의존성 단위 테스트 (cpu, mock).

backend/api/deps.py 의 get_current_space_id / get_current_user 가 미인증 요청을
401 로 막는지. Request.state 를 가짜로 구성 (FastAPI 안 띄움).
멀티테넌트 단계 A (2026-06-03).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.api.deps import get_current_space_id, get_current_user


def _fake_request(auth):
    """request.state.auth 만 가진 가짜 Request."""
    return SimpleNamespace(state=SimpleNamespace(auth=auth))


def test_get_current_space_id_returns_uuid_when_authed():
    sid = str(uuid.uuid4())
    req = _fake_request({"sub": str(uuid.uuid4()), "space": sid})
    assert get_current_space_id(req) == uuid.UUID(sid)


def test_get_current_space_id_401_when_no_auth():
    with pytest.raises(HTTPException) as e:
        get_current_space_id(_fake_request(None))
    assert e.value.status_code == 401


def test_get_current_space_id_401_when_space_missing():
    with pytest.raises(HTTPException) as e:
        get_current_space_id(_fake_request({"sub": "x"}))  # space claim 없음
    assert e.value.status_code == 401


def test_get_current_space_id_401_on_bad_uuid():
    with pytest.raises(HTTPException) as e:
        get_current_space_id(_fake_request({"space": "not-a-uuid"}))
    assert e.value.status_code == 401


@pytest.mark.asyncio
async def test_get_current_user_401_when_no_auth():
    with pytest.raises(HTTPException) as e:
        await get_current_user(_fake_request(None), session=None)  # type: ignore[arg-type]
    assert e.value.status_code == 401


@pytest.mark.asyncio
async def test_get_current_user_401_when_user_not_found(monkeypatch):
    # auth 는 있지만 DB 에 그 user 가 없으면 401.
    async def _none(session, *, user_id):
        return None

    monkeypatch.setattr("backend.api.deps.repository.get_user_by_id", _none)
    req = _fake_request({"sub": str(uuid.uuid4()), "space": str(uuid.uuid4())})
    with pytest.raises(HTTPException) as e:
        await get_current_user(req, session=object())  # type: ignore[arg-type]
    assert e.value.status_code == 401


@pytest.mark.asyncio
async def test_get_current_user_returns_user_when_found(monkeypatch):
    uid = uuid.uuid4()
    fake_user = {"id": uid, "email": "a@b.c", "display_name": None}

    async def _found(session, *, user_id):
        assert user_id == uid
        return fake_user

    monkeypatch.setattr("backend.api.deps.repository.get_user_by_id", _found)
    req = _fake_request({"sub": str(uid), "space": str(uuid.uuid4())})
    user = await get_current_user(req, session=object())  # type: ignore[arg-type]
    assert user == fake_user
