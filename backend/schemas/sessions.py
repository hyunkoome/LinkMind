"""
ask 대화 세션 DB화 스키마 (2026-06-03 단계 B).

frontend askStore(localStorage)의 AskStoreData 와 1:1. write-through 미러(PUT /sessions/sync)
로 서버에 사본을 둔다. 프라이버시: 세션/메시지는 소유자 본인만 조회, 프로젝트는 조직 공유,
학습은 space 전체. (camelCase 필드는 frontend JSON 그대로 받음)
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AskMessageIn(BaseModel):
    role: str
    content: str
    ts: str | None = None
    llm_model: str | None = None
    citations: list[dict[str, Any]] = Field(default_factory=list)
    related_wikis: list[dict[str, Any]] = Field(default_factory=list)
    ingested: list[dict[str, Any]] = Field(default_factory=list)


class AskSessionIn(BaseModel):
    id: str
    title: str | None = None
    projectId: str | None = None
    createdAt: int | None = None
    updatedAt: int | None = None
    messages: list[AskMessageIn] = Field(default_factory=list)


class AskProjectIn(BaseModel):
    id: str
    name: str
    createdAt: int | None = None


class AskStoreSync(BaseModel):
    """write-through 미러 — 현재 user 의 전체 대화 store 를 서버에 덮어쓴다."""
    projects: list[AskProjectIn] = Field(default_factory=list)
    sessions: list[AskSessionIn] = Field(default_factory=list)


# ── 조회 응답 ──────────────────────────────────────────────────

class AskSessionSummary(BaseModel):
    """본인 세션 목록 (메시지 제외)."""
    id: str
    title: str | None = None
    project_id: str | None = None
    created_at_ms: int | None = None
    updated_at_ms: int | None = None
    message_count: int = 0
