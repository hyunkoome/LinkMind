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
    "document",   # Phase 2.5 wave-3 — DOCX/PPTX/TXT/MD 등 (PDF 외 office/text). PDF 는 기존 "pdf" 유지.
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


# ──────────────────────────────────────────────────────────────
# Items (GET/PATCH /items/{id}) — Phase 2.5, user_notes / is_read 도입
# ──────────────────────────────────────────────────────────────


class ItemAttachmentSummary(BaseModel):
    """item 의 첨부 요약 — modality viewer 용 (raw 본문은 /files/{hash} 로 따로)."""
    id: UUID
    role: str | None = None             # 'figure' | 'thumbnail' | 'pdf_source' | 'attachment' …
    mime_type: str | None = None
    file_size: int | None = None
    file_hash: str
    caption: str | None = None
    width: int | None = None
    height: int | None = None


class ItemDetail(BaseModel):
    """item 의 전체 정보 — graph UI modality viewer / 상세 페이지용.

    raw_content 가 큼 (논문 PDF 추출 수십~수백 KB) — 일반 검색 결과엔 미포함,
    여기 GET /items/{id} 에서만 반환.
    """
    id: UUID
    source_type: SourceType
    source_id: str | None = None
    source_url: str | None = None
    source_metadata: dict[str, Any] = Field(default_factory=dict)

    title: str | None = None
    summary: str | None = None
    raw_content: str

    categories: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    language: str | None = None

    source_created_at: datetime | None = None
    ingested_at: datetime
    updated_at: datetime

    # Phase 2.5 신규 — 사용자 메모 + 읽음 inbox
    user_notes: str | None = None
    user_notes_updated_at: datetime | None = None
    is_read: bool = False
    read_at: datetime | None = None

    attachments: list[ItemAttachmentSummary] = Field(default_factory=list)


class ItemUpdateRequest(BaseModel):
    """PATCH /items/{id} — 사용자가 편집 가능한 필드만 (partial update).

    필드 동작:
    - None 또는 미포함  → 변경 없음
    - user_notes=""     → 메모 비움 (NULL 로 설정)
    - user_notes="..."  → 그 내용으로 저장 (+ user_notes_updated_at 자동 갱신)
    - is_read=True      → 읽음 처리 (+ read_at 이 NULL 이면 첫 read 시각으로 채움)
    - is_read=False     → 안 읽음 (read_at 은 그대로 보존 — "처음 읽은 시각" history)
    """
    user_notes: str | None = None
    is_read: bool | None = None
