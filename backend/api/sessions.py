"""
ask 대화 세션 API (2026-06-03 단계 B) — write-through 미러 + 본인 조회.

프라이버시 모델 (사용자 확정):
  - 세션/메시지: 소유자 본인만 조회 (admin 도 남의 대화 못 봄) — repository 가 user_id 강제.
  - 프로젝트: 조직(space) 공유 — 모든 멤버가 봄.
  - 학습 export 는 space 전체 (별도 job, Phase 5) — 보기 권한과 학습 사용 분리.

frontend 는 localStorage 를 그대로 두고 PUT /sessions/sync 로 서버에 사본을 미러한다.
서버 조회(GET)는 멀티턴 디버깅 + 향후 멀티기기 복원/학습 export 의 토대.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_space_id, get_current_user
from backend.db import repository
from backend.db.connection import get_session
from backend.schemas.sessions import AskStoreSync

router = APIRouter()


@router.put("/sync")
async def sync_sessions(
    body: AskStoreSync,
    user: dict = Depends(get_current_user),
    space_id: UUID = Depends(get_current_space_id),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """현재 user 의 전체 대화 store 를 서버에 덮어쓴다 (write-through 미러)."""
    await repository.sync_user_ask_store(
        session,
        user_id=user["id"],
        space_id=space_id,
        projects=[p.model_dump() for p in body.projects],
        sessions=[s.model_dump() for s in body.sessions],
    )
    await session.commit()
    return {"ok": True, "projects": len(body.projects), "sessions": len(body.sessions)}


@router.get("")
async def list_sessions(
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """본인 세션 목록 (메시지 제외, 가벼움). 멀티턴 디버깅용."""
    return await repository.list_user_ask_sessions(session, user_id=user["id"])


@router.get("/export")
async def export_store(
    user: dict = Depends(get_current_user),
    space_id: UUID = Depends(get_current_space_id),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """frontend localStorage 복원용 — 본인 세션 전체(+메시지) + 조직 공유 프로젝트.

    로그인 시 이걸 받아 계정별 localStorage 를 덮어쓴다 → 다른 브라우저/다른 계정에서도
    정확히 본인 대화만 보이게. AskStoreData 형태(camelCase)로 반환.
    """
    sessions = await repository.get_user_ask_store_full(session, user_id=user["id"])
    projects = await repository.list_space_ask_projects(session, space_id=space_id)
    return {
        "projects": [
            {"id": p["id"], "name": p["name"], "createdAt": p.get("created_at_ms")}
            for p in projects
        ],
        "sessions": [
            {
                "id": s["id"],
                "title": s.get("title"),
                "projectId": s.get("project_id"),
                "createdAt": s.get("created_at_ms"),
                "updatedAt": s.get("updated_at_ms"),
                "messages": [
                    {
                        "role": m["role"],
                        "content": m["content"],
                        "ts": m.get("ts"),
                        "llm_model": m.get("llm_model"),
                        "citations": m.get("citations") or [],
                        "related_wikis": m.get("related_wikis") or [],
                        "ingested": m.get("ingested") or [],
                    }
                    for m in s["messages"]
                ],
            }
            for s in sessions
        ],
    }


@router.get("/projects")
async def list_projects(
    space_id: UUID = Depends(get_current_space_id),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """조직 공유 프로젝트 목록 (space 전체)."""
    return await repository.list_space_ask_projects(session, space_id=space_id)


@router.get("/{session_id}")
async def get_session_detail(
    session_id: str,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """본인 세션 상세 (메시지 포함). 남의 세션이면 404 — 프라이버시."""
    detail = await repository.get_user_ask_session(
        session, user_id=user["id"], session_id=session_id
    )
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="세션을 찾을 수 없습니다")
    return detail
