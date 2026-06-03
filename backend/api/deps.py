"""
FastAPI 인증 의존성 (2026-06-03 단계 A).

요청 흐름:
  AuthMiddleware (api/middleware.py) 가 쿠키 JWT 를 디코드 → request.state.auth = payload
  (없거나 만료면 None). 여기 deps 는 그 state 를 읽어 인증/멤버십을 강제한다.

  - get_current_user  : 로그인 필수. user dict 반환. 미인증 → 401.
  - get_current_space_id : 활성 space_id (JWT 의 space claim). 미인증 → 401.

RLS SET LOCAL 은 get_session(connection.py) 이 request.state.auth 로 수행 — 여기선 안 함.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db import repository
from backend.db.connection import get_session


def _auth_payload(request: Request) -> dict | None:
    return getattr(request.state, "auth", None)


async def get_current_user(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """로그인한 user dict (id, email, display_name). 미인증/유효하지 않으면 401."""
    payload = _auth_payload(request)
    if not payload or "sub" not in payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="인증이 필요합니다")
    try:
        user_id = UUID(str(payload["sub"]))
    except (ValueError, TypeError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="잘못된 토큰")
    user = await repository.get_user_by_id(session, user_id=user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="존재하지 않는 사용자")
    return user


def get_current_space_id(request: Request) -> UUID:
    """JWT 의 활성 space_id. 미인증 → 401. (멤버십은 로그인/switch 시점에 검증됨)."""
    payload = _auth_payload(request)
    if not payload or "space" not in payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="인증이 필요합니다")
    try:
        return UUID(str(payload["space"]))
    except (ValueError, TypeError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="잘못된 토큰")


async def require_space_admin(
    user: dict = Depends(get_current_user),
    space_id: UUID = Depends(get_current_space_id),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """현재 space 의 루트 관리자(owner/admin)만 통과. 일반 멤버/미인증 → 403/401.

    운영 모델(2026-06-03): self-signup 없음 — 루트 관리자가 멤버 계정을 발급한다.
    """
    role = await repository.get_member_role(session, space_id=space_id, user_id=user["id"])
    if role not in ("owner", "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="조직 관리자만 가능합니다"
        )
    return user
