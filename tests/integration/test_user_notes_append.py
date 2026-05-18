"""
backend.db.repository.append_item_user_notes 의 SQL 동작 검증 — integration.

CLAUDE.md §9: Postgres 가 필요한 함수의 변경이면 integration marker.

검증 시나리오 (Phase 2.5 wave-3 caption 정책):
  1. 빈 user_notes 에 새 note 추가 → 그대로 set (timestamp 안 붙음)
  2. 이미 있는 user_notes 에 새 note 추가 → "<old>\n\n--- YYYY-MM-DD HH:MM ---\n<new>"
  3. 같은 note 를 두 번 던지면 idempotent — 두 번째는 변경 없음 (rowcount 0)
  4. 빈/None new_note 면 no-op
  5. user_notes_updated_at 이 갱신됨

테스트는 일회용 item 을 INSERT 한 뒤 검증, 끝나면 DELETE 로 깨끗이 정리.
"""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.db.repository import append_item_user_notes


def _resolve_db_url() -> str:
    """테스트 실행 환경(호스트) 에서 접근 가능한 Postgres URL.

    LINKMIND_DATABASE_URL 우선, 없으면 localhost:5432 default — backend.config 와
    같은 패턴 (effective_database_url 의 RUNNING_IN_DOCKER 분기). 호스트 셸에서
    pytest 가 도는 게 일반적이라 localhost 가 맞음.
    """
    return os.getenv(
        "LINKMIND_DATABASE_URL",
        "postgresql+asyncpg://linkmind:real2real@localhost:5432/linkmind",
    )


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    """단일 테스트용 AsyncSession — Postgres 가 안 떠있으면 skip."""
    db_url = _resolve_db_url()
    engine = create_async_engine(db_url, echo=False)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as e:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f"Postgres 미가동 ({db_url}): {type(e).__name__}: {e}")
    sf = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with sf() as s:
        yield s
    await engine.dispose()


async def _insert_test_item(session: AsyncSession) -> str:
    """source_type='url' 의 일회용 placeholder item INSERT — 끝나면 호출자가 cleanup."""
    item_id = str(uuid4())
    raw = f"[caption test placeholder] {item_id}"
    await session.execute(
        text("""
            INSERT INTO items (
                id, source_type, raw_content, raw_content_hash, source_id,
                source_url, title
            ) VALUES (
                :id, 'url', :raw, :h, :src_id, NULL, 'caption test'
            )
        """),
        {"id": item_id, "raw": raw, "h": f"test-{item_id}", "src_id": item_id},
    )
    await session.commit()
    return item_id


async def _cleanup_test_item(session: AsyncSession, item_id: str) -> None:
    await session.execute(text("DELETE FROM items WHERE id = :id"), {"id": item_id})
    await session.commit()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_append_to_empty_user_notes(session: AsyncSession):
    """빈 user_notes 에 새 note → 그대로 set (timestamp 구분자 안 붙음)."""
    item_id = await _insert_test_item(session)
    try:
        changed = await append_item_user_notes(
            session, item_id=item_id, new_note="첫 메모입니다",
        )
        await session.commit()
        assert changed is True
        row = await session.execute(
            text("SELECT user_notes, user_notes_updated_at FROM items WHERE id = :id"),
            {"id": item_id},
        )
        notes, updated_at = row.one()
        assert notes == "첫 메모입니다"
        assert updated_at is not None
        assert "---" not in notes
    finally:
        await _cleanup_test_item(session, item_id)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_append_preserves_existing_note(session: AsyncSession):
    """기존 메모가 있으면 timestamp 구분자와 함께 append (덮어쓰기 X)."""
    item_id = await _insert_test_item(session)
    try:
        await append_item_user_notes(
            session, item_id=item_id, new_note="첫 메모",
        )
        await session.commit()
        changed2 = await append_item_user_notes(
            session, item_id=item_id, new_note="두 번째 메모 — 다른 내용",
        )
        await session.commit()
        assert changed2 is True
        notes = (
            await session.execute(
                text("SELECT user_notes FROM items WHERE id = :id"),
                {"id": item_id},
            )
        ).scalar_one()
        assert notes.startswith("첫 메모")
        assert "두 번째 메모 — 다른 내용" in notes
        assert "---" in notes  # timestamp 구분자
    finally:
        await _cleanup_test_item(session, item_id)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_append_same_note_is_idempotent(session: AsyncSession):
    """같은 caption 을 두 번 던지면 두 번째는 변경 없음 (텔레그램 retry 안전)."""
    item_id = await _insert_test_item(session)
    try:
        await append_item_user_notes(
            session, item_id=item_id, new_note="중복 caption",
        )
        await session.commit()
        changed2 = await append_item_user_notes(
            session, item_id=item_id, new_note="중복 caption",
        )
        await session.commit()
        assert changed2 is False
        notes = (
            await session.execute(
                text("SELECT user_notes FROM items WHERE id = :id"),
                {"id": item_id},
            )
        ).scalar_one()
        # 한 번만 들어있어야 — append 두 번이면 두 번 들어가 있을 것
        assert notes.count("중복 caption") == 1
    finally:
        await _cleanup_test_item(session, item_id)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_append_empty_note_is_noop(session: AsyncSession):
    """빈/whitespace-only new_note 는 no-op (False)."""
    item_id = await _insert_test_item(session)
    try:
        assert await append_item_user_notes(
            session, item_id=item_id, new_note=None,
        ) is False
        assert await append_item_user_notes(
            session, item_id=item_id, new_note="",
        ) is False
        assert await append_item_user_notes(
            session, item_id=item_id, new_note="   ",
        ) is False
        notes = (
            await session.execute(
                text("SELECT user_notes FROM items WHERE id = :id"),
                {"id": item_id},
            )
        ).scalar_one()
        assert notes is None
    finally:
        await _cleanup_test_item(session, item_id)
