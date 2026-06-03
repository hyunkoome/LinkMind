"""
인증 보안 primitive 단위 테스트 (cpu, mock 없음 — 순수 함수).

backend/auth/security.py 의 bcrypt 해시 + JWT(HS256) round-trip.
멀티테넌트 단계 A (2026-06-03).
"""

from __future__ import annotations

import uuid

import pytest

from backend.auth.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


def test_hash_then_verify_roundtrip():
    h = hash_password("linkmind-secret")
    assert h != "linkmind-secret"            # 평문 저장 금지
    assert verify_password("linkmind-secret", h) is True


def test_verify_rejects_wrong_password():
    h = hash_password("correct")
    assert verify_password("wrong", h) is False


def test_verify_handles_malformed_hash():
    # 깨진 해시 포맷이어도 예외 대신 False.
    assert verify_password("anything", "not-a-bcrypt-hash") is False


def test_hash_is_salted_unique():
    # 같은 평문이라도 salt 로 매번 다른 해시 (둘 다 검증은 통과).
    a = hash_password("same")
    b = hash_password("same")
    assert a != b
    assert verify_password("same", a) and verify_password("same", b)


def test_jwt_encode_decode_roundtrip():
    uid, sid = str(uuid.uuid4()), str(uuid.uuid4())
    token = create_access_token(user_id=uid, space_id=sid)
    payload = decode_access_token(token)
    assert payload is not None
    assert payload["sub"] == uid
    assert payload["space"] == sid
    assert "exp" in payload


def test_jwt_decode_rejects_garbage():
    assert decode_access_token("not.a.jwt") is None
    assert decode_access_token("") is None


def test_jwt_decode_rejects_wrong_signature():
    # 다른 secret 으로 서명된 토큰은 거부돼야 한다.
    import jwt as _jwt
    forged = _jwt.encode({"sub": "x", "space": "y"}, "attacker-secret", algorithm="HS256")
    assert decode_access_token(forged) is None


def test_jwt_decode_rejects_expired():
    # exp 가 과거인 토큰 → None. (직접 음수 만료로 인코딩)
    import jwt as _jwt
    from datetime import datetime, timedelta, timezone
    from backend.config import get_settings

    s = get_settings()
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    expired = _jwt.encode(
        {"sub": "x", "space": "y", "exp": past},
        s.linkmind_jwt_secret,
        algorithm=s.linkmind_jwt_algorithm,
    )
    assert decode_access_token(expired) is None
