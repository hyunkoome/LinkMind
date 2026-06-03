"""
인증 보안 primitive — 비밀번호 해시(bcrypt) + JWT(HS256).

passlib 대신 bcrypt 를 직접 쓴다 (passlib 유지보수 중단 + bcrypt 4.x 마찰, CLAUDE.md/plan).
JWT secret/만료/알고리즘은 모두 config (env) 에서 — 코드 하드코딩 금지 (§4).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

import bcrypt
import jwt

from backend.config import get_settings


# ── 비밀번호 ──────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    """평문 → bcrypt 해시 (str). bcrypt 는 72바이트 초과분을 무시하므로 그대로 인코딩."""
    digest = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
    return digest.decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """평문이 해시와 일치하는지. 잘못된 해시 포맷은 False (예외 삼킴)."""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ── JWT ──────────────────────────────────────────────────────────

def create_access_token(*, user_id: UUID | str, space_id: UUID | str) -> str:
    """payload = {sub: user_id, space: active_space_id, exp}. httpOnly 쿠키로만 전달."""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "space": str(space_id),
        "iat": now,
        "exp": now + timedelta(hours=settings.linkmind_jwt_expiration_hours),
    }
    return jwt.encode(payload, settings.linkmind_jwt_secret, algorithm=settings.linkmind_jwt_algorithm)


def decode_access_token(token: str) -> dict | None:
    """JWT 검증 → payload. 만료/서명불일치/형식오류면 None."""
    settings = get_settings()
    try:
        return jwt.decode(
            token,
            settings.linkmind_jwt_secret,
            algorithms=[settings.linkmind_jwt_algorithm],
        )
    except jwt.PyJWTError:
        return None
