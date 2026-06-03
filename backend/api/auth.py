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

from uuid import UUID as _UUID

from backend.api.deps import (
    get_current_space_id,
    get_current_user,
    require_space_admin,
)
from backend.auth.security import create_access_token, hash_password, verify_password
from backend.config import get_settings
from backend.db import repository
from backend.db.connection import get_session
from backend.schemas.auth import (
    AdminCreateUserRequest,
    BootstrapRequest,
    ChangeCredentialsRequest,
    LoginRequest,
    MemberOut,
    SpaceOut,
    SwitchSpaceRequest,
    UserOut,
)

router = APIRouter()


@router.get("/bootstrap-needed")
async def bootstrap_needed(session: AsyncSession = Depends(get_session)) -> dict:
    """첫 관리자 등록이 필요한지 (user 0명). frontend 로그인 페이지가 이걸로 분기."""
    return {"needed": (await repository.count_users(session)) == 0}


@router.post("/bootstrap", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def bootstrap(
    body: BootstrapRequest,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> UserOut:
    """첫 관리자 + 조직 space 생성 — user 가 0명일 때만. 그 후 self-signup 은 영구 비활성.

    설치 후 브라우저에서 고객 조직이 직접 첫 관리자를 만든다 (운영자는 인프라만).
    """
    if (await repository.count_users(session)) != 0:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="이미 초기화된 인스턴스입니다 — 관리자에게 계정 발급을 요청하세요",
        )
    email = body.email.strip()
    user_id = await repository.create_user(
        session,
        email=email,
        password_hash=hash_password(body.password),
        display_name=body.display_name,
    )
    space_id = await repository.create_space(
        session, name=body.org_name.strip(), kind="org"
    )
    await repository.add_member(session, space_id=space_id, user_id=user_id, role="owner")
    await session.commit()

    token = create_access_token(user_id=user_id, space_id=space_id)
    _set_auth_cookie(response, token)
    spaces = await repository.list_user_spaces(session, user_id=user_id)
    user = {"id": user_id, "email": email, "display_name": body.display_name}
    return _build_user_out(user, space_id, spaces)


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
        must_change_password=bool(user.get("must_change_password", False)),
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


@router.post("/admin/users", response_model=MemberOut, status_code=status.HTTP_201_CREATED)
async def admin_create_user(
    body: AdminCreateUserRequest,
    admin: dict = Depends(require_space_admin),
    space_id: _UUID = Depends(get_current_space_id),
    session: AsyncSession = Depends(get_session),
) -> MemberOut:
    """루트 관리자가 멤버 계정 발급 — 새 user 를 *관리자의 현재 조직 space* 에 합류.

    운영 모델(2026-06-03): self-signup 없음. 멤버는 조직 space 를 공유 → 같은 데이터.
    새 space 를 만들지 않고 기존 조직 space 에 member(또는 admin) 로 추가한다.
    """
    email = body.email.strip()
    if await repository.get_user_by_email(session, email=email) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="이미 등록된 이메일입니다"
        )
    role = body.role if body.role in ("member", "admin") else "member"
    user_id = await repository.create_user(
        session,
        email=email,
        password_hash=hash_password(body.password),
        display_name=body.display_name,
        must_change_password=True,   # 발급된 초기 비번 → 멤버 첫 로그인 시 강제 변경
    )
    await repository.add_member(session, space_id=space_id, user_id=user_id, role=role)
    await session.commit()
    return MemberOut(
        id=user_id, email=email, display_name=body.display_name, role=role
    )


@router.get("/admin/members", response_model=list[MemberOut])
async def admin_list_members(
    admin: dict = Depends(require_space_admin),
    space_id: _UUID = Depends(get_current_space_id),
    session: AsyncSession = Depends(get_session),
) -> list[MemberOut]:
    members = await repository.list_space_members(session, space_id=space_id)
    return [
        MemberOut(
            id=m["id"], email=m["email"], display_name=m.get("display_name"), role=m["role"]
        )
        for m in members
    ]


@router.delete("/admin/users/{user_id}")
async def admin_delete_user(
    user_id: _UUID,
    admin: dict = Depends(require_space_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    # 자기 자신은 삭제 불가 (조직에 관리자 0명 되는 것 방지).
    if user_id == admin["id"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="자기 자신은 삭제할 수 없습니다"
        )
    await repository.delete_user(session, user_id=user_id)
    await session.commit()
    return {"ok": True, "deleted_user_id": str(user_id)}


@router.post("/change-credentials", response_model=UserOut)
async def change_credentials(
    body: ChangeCredentialsRequest,
    response: Response,
    user: dict = Depends(get_current_user),
    active_space_id: UUID = Depends(get_current_space_id),
    session: AsyncSession = Depends(get_session),
) -> UserOut:
    """첫 로그인 강제 변경 — 현재 비번 확인 후 새 비번(필수)/이메일(선택). must_change_password 해제.
    토큰도 재발급(쿠키 갱신)."""
    full = await repository.get_user_by_email(session, email=user["email"])
    if full is None or not verify_password(body.current_password, full["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="현재 비밀번호가 올바르지 않습니다"
        )
    new_email = body.new_email.strip() if body.new_email else None
    if new_email and new_email != user["email"]:
        if await repository.get_user_by_email(session, email=new_email) is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="이미 사용 중인 이메일입니다"
            )
    else:
        new_email = None
    await repository.update_user_credentials(
        session,
        user_id=user["id"],
        new_email=new_email,
        new_password_hash=hash_password(body.new_password),
    )
    await session.commit()
    token = create_access_token(user_id=user["id"], space_id=active_space_id)
    _set_auth_cookie(response, token)
    updated = await repository.get_user_by_id(session, user_id=user["id"])
    spaces = await repository.list_user_spaces(session, user_id=user["id"])
    return _build_user_out(updated or user, active_space_id, spaces)


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
