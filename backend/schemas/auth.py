"""인증/멀티테넌트 API 스키마 (2026-06-03 단계 A)."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    # email 은 str — 식별자 매칭용. EmailStr 의 엄격한 검증(.local 등 special-use 도메인
    # 거부)은 self-host 마찰만 크다.
    email: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class BootstrapRequest(BaseModel):
    """첫 관리자 등록 — 인스턴스에 user 가 0명일 때만 허용. 루트 관리자 + 조직 space 생성."""
    org_name: str = Field(..., min_length=1, max_length=120)
    email: str = Field(..., min_length=3)
    password: str = Field(..., min_length=6)
    display_name: str | None = Field(default=None, max_length=80)


class AdminCreateUserRequest(BaseModel):
    """루트 관리자가 멤버 발급 — 새 user 를 관리자의 현재 조직 space 에 합류시킴.
    self-signup 없음(2026-06-03 운영 모델): 멤버 계정은 관리자만 생성한다."""
    email: str = Field(..., min_length=3)
    password: str = Field(..., min_length=6)
    display_name: str | None = Field(default=None, max_length=80)
    role: str = Field(default="member")   # 'member' | 'admin' (조직 내 권한)


class MemberOut(BaseModel):
    """조직 멤버 — 루트 관리자 유저 관리 UI 용."""
    id: UUID
    email: str
    display_name: str | None = None
    role: str


class SpaceOut(BaseModel):
    id: UUID
    name: str
    kind: str
    role: str | None = None        # 현재 user 의 그 space 내 역할


class UserOut(BaseModel):
    """GET /auth/me — 현재 로그인 user + 활성 space + 소속 space 목록."""
    id: UUID
    email: str
    display_name: str | None = None
    active_space_id: UUID
    spaces: list[SpaceOut] = Field(default_factory=list)


class SwitchSpaceRequest(BaseModel):
    space_id: UUID
