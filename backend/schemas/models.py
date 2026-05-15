"""
LinkMind API용 Pydantic 스키마.

DB 컬럼과 1:1 매핑되지 않고, 외부 노출에 맞춰 정제한 형태.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl

# ──────────────────────────────────────────────────────────────
# Ingestion
# ──────────────────────────────────────────────────────────────

SourceType = Literal[
    "slack", "telegram", "url", "pdf",
    "github", "arxiv", "youtube", "youtube_playlist", "manual",
]


class IngestRequest(BaseModel):
    """수집 요청 — 최소한의 필드.

    OpenClaw extension이나 외부 client가 이 형태로 POST한다.
    raw_content가 필수임에 주목 (raw-first 원칙).
    """
    source_type: SourceType
    raw_content: str = Field(..., min_length=1, description="원본 텍스트 (변형 금지)")
    source_id: str | None = None
    source_url: HttpUrl | str | None = None
    source_metadata: dict[str, Any] = Field(default_factory=dict)
    title: str | None = None
    source_created_at: datetime | None = None

    # 분석을 클라이언트가 trigger하고 싶을 때
    analyze_now: bool = Field(default=True, description="True면 즉시 요약/태깅/임베딩 수행")


class IngestResponse(BaseModel):
    item_id: UUID
    created: bool = Field(description="True면 신규, False면 동일 hash로 이미 존재")
    chunks_indexed: int = 0


# ──────────────────────────────────────────────────────────────
# Search
# ──────────────────────────────────────────────────────────────


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    top_k: int = Field(default=10, ge=1, le=100)
    source_types: list[SourceType] | None = None
    categories: list[str] | None = None
    tags: list[str] | None = None


class SearchHit(BaseModel):
    item_id: UUID
    chunk_id: UUID | None = None
    score: float
    title: str | None = None
    summary: str | None = None
    snippet: str | None = None
    source_type: SourceType
    source_url: str | None = None
    categories: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class SearchResponse(BaseModel):
    query: str
    hits: list[SearchHit]


# ──────────────────────────────────────────────────────────────
# Ask (RAG)
# ──────────────────────────────────────────────────────────────


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1)
    top_k: int = Field(default=8, ge=1, le=30)
    llm_provider: Literal["openai", "claude", "ollama"] | None = None
    llm_model: str | None = None


class AskCitation(BaseModel):
    item_id: UUID
    title: str | None = None
    source_url: str | None = None
    snippet: str | None = None


class AskResponse(BaseModel):
    question: str
    answer: str
    citations: list[AskCitation] = Field(default_factory=list)
    llm_provider: str
    llm_model: str
