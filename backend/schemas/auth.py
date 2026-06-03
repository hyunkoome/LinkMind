"""인증/멀티테넌트 API 스키마 (2026-06-03 단계 A)."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    # email 은 str — 식별자 매칭용. EmailStr 의 엄격한 검증(.local 등 special-use 도메인
    # 거부)은 self-host 마찰만 크다.
    email: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class RegisterRequest(BaseModel):
    """회원가입 — 새 user + 본인 personal space 자동 생성 + 자동 로그인."""
    email: str = Field(..., min_length=3)
    password: str = Field(..., min_length=6)
    display_name: str | None = Field(default=None, max_length=80)


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
