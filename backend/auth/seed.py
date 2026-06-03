"""
기본 루트 관리자 + 조직 space seed (2026-06-03 단계 A).

운영 모델: 1 조직 = 1 space, 멤버 전원이 같은 데이터 공유. self-signup 없음 —
첫 부팅 시 루트 관리자(owner) 1명 + 조직 space 1개를 만든다. 이후 멤버는 루트가 발급.
이메일/비밀번호/조직명은 config(env). lifespan 에서 호출 (main.py). idempotent.
"""

from __future__ import annotations

import logging
from uuid import UUID

from backend.auth.security import hash_password
from backend.config import get_settings
from backend.db import repository
from backend.db.connection import get_session_factory

logger = logging.getLogger("linkmind.auth.seed")


async def seed_default_user_space() -> UUID | None:
    """기본 user + personal space + owner 멤버십 보장. 생성했으면 space_id 반환, 이미 있으면 None.

    반환된 space_id 는 단계 C 의 기존 데이터 마이그레이션(기본 space 백필)에서 재사용된다.
    """
    settings = get_settings()
    factory = get_session_factory()
    async with factory() as session:
        existing = await repository.get_user_by_email(
            session, email=settings.linkmind_seed_user_email
        )
        if existing is not None:
            return None

        user_id = await repository.create_user(
            session,
            email=settings.linkmind_seed_user_email,
            password_hash=hash_password(settings.linkmind_seed_user_password),
            display_name=None,
        )
        space_id = await repository.create_space(
            session, name=settings.linkmind_seed_space_name, kind="org"
        )
        await repository.add_member(
            session, space_id=space_id, user_id=user_id, role="owner"
        )
        await session.commit()
        logger.info(
            "루트 관리자 + 조직 space seed 완료 — email=%s, org=%s (%s). "
            "로그인 후 비밀번호 변경 + Settings 에서 멤버 발급.",
            settings.linkmind_seed_user_email, settings.linkmind_seed_space_name, space_id,
        )
        return space_id
