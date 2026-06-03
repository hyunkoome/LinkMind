"""
인증 API (2026-06-03 단계 A) — id/pw 로그인 + JWT httpOnly 쿠키.

엔드포인트:
  POST /auth/login         (공개) — bcrypt 검증 → JWT 발급 → Set-Cookie → UserOut
  POST /auth/logout        (공개) — 쿠키 삭제
  GET  /auth/me            (보호) — 현재 user + 활성 space + 소속 space 목록
  POST /auth/switch-space  (보호) — 멤버인 다른 space 로 활성 전환 (JWT 재발급)

토큰은 httpOnly 쿠키로만 전달 (XSS 안전). 응답 본문엔 토큰을 넣지 않는다.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_space_id, get_current_user
from backend.auth.security import create_access_token, hash_password, verify_password
from backend.config import get_settings
from backend.db import repository
from backend.db.connection import get_session
from backend.schemas.auth import (
    LoginRequest,
    RegisterRequest,
    SpaceOut,
    SwitchSpaceRequest,
    UserOut,
)

router = APIRouter()


def _set_auth_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=settings.linkmind_cookie_name,
        value=token,
        httponly=True,
        secure=settings.linkmind_cookie_secure,
        samesite=settings.linkmind_cookie_samesite,
        max_age=settings.linkmind_jwt_expiration_hours * 3600,
        path="/",
    )


def _build_user_out(user: dict, active_space_id: UUID, spaces: list[dict]) -> UserOut:
    return UserOut(
        id=user["id"],
        email=user["email"],
        display_name=user.get("display_name"),
        active_space_id=active_space_id,
        spaces=[
            SpaceOut(id=s["id"], name=s["name"], kind=s["kind"], role=s.get("role"))
            for s in spaces
        ],
    )


@router.post("/login", response_model=UserOut)
async def login(
    body: LoginRequest,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> UserOut:
    user = await repository.get_user_by_email(session, email=body.email)
    # 타이밍 차이를 줄이기 위해 user 없음/비번 불일치 모두 동일 메시지.
    if user is None or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="이메일 또는 비밀번호가 올바르지 않습니다",
        )
    spaces = await repository.list_user_spaces(session, user_id=user["id"])
    if not spaces:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="소속된 space 가 없습니다"
        )
    active_space_id = spaces[0]["id"]  # 가입 첫 space 가 기본 활성
    token = create_access_token(user_id=user["id"], space_id=active_space_id)
    _set_auth_cookie(response, token)
    return _build_user_out(user, active_space_id, spaces)


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> UserOut:
    """회원가입 — 새 user + 본인 personal space 자동 생성 + 자동 로그인(쿠키).

    멀티테넌트 통합 모델: 가입하면 멤버1 personal space 를 가진다 (조직은 이후 초대로 합류).
    """
    email = body.email.strip()
    if await repository.get_user_by_email(session, email=email) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="이미 등록된 이메일입니다"
        )
    user_id = await repository.create_user(
        session,
        email=email,
        password_hash=hash_password(body.password),
        display_name=body.display_name,
    )
    space_name = (body.display_name or email.split("@")[0]).strip() + " Space"
    space_id = await repository.create_space(session, name=space_name, kind="personal")
    await repository.add_member(session, space_id=space_id, user_id=user_id, role="owner")
    await session.commit()

    token = create_access_token(user_id=user_id, space_id=space_id)
    _set_auth_cookie(response, token)
    spaces = await repository.list_user_spaces(session, user_id=user_id)
    user = {"id": user_id, "email": email, "display_name": body.display_name}
    return _build_user_out(user, space_id, spaces)


@router.post("/logout")
async def logout(response: Response) -> dict:
    settings = get_settings()
    response.delete_cookie(key=settings.linkmind_cookie_name, path="/")
    return {"ok": True}


@router.get("/me", response_model=UserOut)
async def me(
    user: dict = Depends(get_current_user),
    active_space_id: UUID = Depends(get_current_space_id),
    session: AsyncSession = Depends(get_session),
) -> UserOut:
    spaces = await repository.list_user_spaces(session, user_id=user["id"])
    return _build_user_out(user, active_space_id, spaces)


@router.post("/switch-space", response_model=UserOut)
async def switch_space(
    body: SwitchSpaceRequest,
    response: Response,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> UserOut:
    # 멤버인 space 로만 전환 가능.
    if not await repository.is_space_member(
        session, space_id=body.space_id, user_id=user["id"]
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="해당 space 의 멤버가 아닙니다"
        )
    token = create_access_token(user_id=user["id"], space_id=body.space_id)
    _set_auth_cookie(response, token)
    spaces = await repository.list_user_spaces(session, user_id=user["id"])
    return _build_user_out(user, body.space_id, spaces)
