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


class AskRelatedWiki(BaseModel):
    """답변의 citations 와 link 된 wiki_pages — frontend 의 /ask 페이지의 우측
    panel 에 표시. citation 의 item_id 들이 어느 wiki 와 연결돼 있는지 집계.
    """
    slug: str
    title: str
    description: str | None = None
    body_status: str
    overlap: int           # 이 wiki 와 link 된 citation item 수 (관련도 신호)


class AskResponse(BaseModel):
    question: str
    answer: str
    citations: list[AskCitation] = Field(default_factory=list)
    related_wikis: list[AskRelatedWiki] = Field(default_factory=list)
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


# ──────────────────────────────────────────────────────────────
# Cleanup list (GET /items) — D12 placeholder 정리 UI
# ──────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────
# Graph (GET /graph/*) — Phase 2.5 wave-3, cytoscape.js 호환 JSON
# ──────────────────────────────────────────────────────────────


class GraphNode(BaseModel):
    """cytoscape 노드. type='topic'|'item' 으로 UI 측 스타일 분기.

    data 의 추가 필드 (item 의 source_type/is_read/tags/has_notes, topic 의 slug/
    item_count) 는 frontend 의 노드 정보 패널 + 색상/모양 결정에 사용.
    """
    data: dict[str, Any]   # cytoscape 표준 — {"id", "label", "type", ...}


class GraphEdge(BaseModel):
    """cytoscape 엣지. data.role = 'paper'|'code'|'video'|'playlist'|'blog'|'note'.

    item-topic 엣지는 source=item, target=topic. (방향성 큰 의미 X, 시각화용)
    """
    data: dict[str, Any]   # {"id", "source", "target", "role", ...}


class GraphResponse(BaseModel):
    """cytoscape elements 표준 — `{nodes: [...], edges: [...]}`.

    빈 응답도 valid (검색 결과 0 건). frontend 가 그래프 비우면 됨.
    """
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)


# ──────────────────────────────────────────────────────────────
# Wiki (GET /wiki/*) — D10 llm_wiki 아키텍처 (2026-05-26)
# docs/llm_wiki_design.md 참조
# ──────────────────────────────────────────────────────────────


class WikiSource(BaseModel):
    """한 wiki page 의 source item."""
    item_id: UUID
    title: str | None = None
    summary: str | None = None
    source_type: str
    source_url: str | None = None
    confidence: float | None = None
    role: str | None = None
    user_action: str | None = None
    tags: list[str] = Field(default_factory=list)
    user_notes: str | None = None
    is_read: bool = False
    attachment_count: int = 0


class WikiCrossLink(BaseModel):
    """다른 wiki 페이지 cross-link 후보."""
    slug: str
    title: str
    shared_items: int


class WikiPageDetail(BaseModel):
    """GET /wiki/{slug} 응답 — wiki page 전체."""
    id: UUID
    topic_id: UUID | None = None
    slug: str
    title: str
    description: str | None = None
    variant: str = "default"
    body: str | None = None
    body_status: str        # 'ready' | 'pending' | 'completed' | 'pending'
    body_model: str | None = None
    body_prompt_version: str | None = None
    body_generated_at: datetime | None = None
    latest_version: int = 0
    is_pinned: bool = False
    user_overrides: str | None = None
    keywords: list[str] = Field(default_factory=list)
    sources: list[WikiSource] = Field(default_factory=list)
    cross_links: list[WikiCrossLink] = Field(default_factory=list)
    user_notes_combined: str | None = None


class WikiKeywordsUpdateRequest(BaseModel):
    """POST /wiki/{slug}/keywords — 키워드 add/remove."""
    add: list[str] = Field(default_factory=list)
    remove: list[str] = Field(default_factory=list)


class WikiKeywordsUpdateResponse(BaseModel):
    slug: str
    keywords: list[str]
    added: list[str]
    removed: list[str]


class WikiKeywordSuggestion(BaseModel):
    keyword: str
    usage_count: int       # 이 키워드가 쓰인 wiki page 수


class WikiKeywordSearchResponse(BaseModel):
    query: str
    suggestions: list[WikiKeywordSuggestion]


class WikiPageListItem(BaseModel):
    """GET /wiki list 의 행."""
    id: UUID
    topic_id: UUID | None = None
    slug: str
    title: str
    description: str | None = None
    body_status: str
    body_generated_at: datetime | None = None
    source_count: int = 0
    is_pinned: bool = False
    updated_at: datetime


class WikiPageListResponse(BaseModel):
    total: int
    pages: list[WikiPageListItem]


class WikiSearchHit(BaseModel):
    """POST /wiki/search 의 한 결과 (wiki page 단위)."""
    page_id: UUID
    slug: str
    title: str
    description: str | None = None
    score: float
    body_excerpt: str | None = None        # body 의 일부 (300자)
    source_count: int = 0
    body_status: str
    matched_in: str = "body"               # 'body' | 'description' | 'sources'


class WikiSearchRequest(BaseModel):
    query: str
    top_k: int = Field(default=10, ge=1, le=100)


class WikiSearchResponse(BaseModel):
    query: str
    hits: list[WikiSearchHit] = Field(default_factory=list)


class WikiRegenerateResponse(BaseModel):
    """POST /wiki/{slug}/regenerate 응답."""
    page_id: UUID
    slug: str
    ok: bool
    body_length: int | None = None
    version_number: int | None = None
    duration_ms: int = 0
    error: str | None = None


class WikiStatsResponse(BaseModel):
    """GET /wiki/_stats — body_status 별 wiki_pages 개수 (frontend tab UI 의 count).

    2026-05-27 통일: 3 status (ready/pending/completed). 옛 4종 (empty/stale/
    generating/ready) 의 통합 — schema migration 동반.
    """
    ready: int = 0           # body 없음, lazy 처리 대기 (옛 'empty')
    pending: int = 0         # 처리 대기/진행 중 (옛 'stale' + 'generating' 통합)
    completed: int = 0       # 처리 완료 (옛 'ready')
    total: int = 0


class WikiBatchRegenerateRequest(BaseModel):
    """POST /wiki/_batch/regenerate — body_status 가 'ready' 또는 'pending' 인
    wiki_pages 를 일괄 합성 (batch CLI 와 동일 효과를 HTTP 로).

    fire-and-forget — request 즉시 응답, BackgroundTask 가 비동기 처리.
    frontend 가 GET /wiki/_stats polling 으로 진행 확인.
    """
    status: str = Field(default="ready", description="ready 또는 pending")
    limit: int = Field(default=10, ge=1, le=50, description="한 번에 처리할 page 수")


class WikiBatchRegenerateResponse(BaseModel):
    status: str
    dispatched: int                       # 실제 dispatch 한 page 수 (limit 보다 적을 수 있음 — 매칭 page 부족)
    estimated_seconds: int                # 대략 — page 당 4초 + concurrency 4 기준


class WikiPageEditRequest(BaseModel):
    """PATCH /wiki/{slug} — title / description / body 수동 편집.

    셋 다 optional. None 으로 보낸 필드는 변경 없음. 적어도 하나는 제공해야.
    body 가 제공되면 body_status='completed' + body_model='user' + 새 version 기록 +
    Qdrant body embedding 재upsert.
    """
    title: str | None = None
    description: str | None = None
    body: str | None = None


class WikiPageDeleteResponse(BaseModel):
    """DELETE /wiki/{slug} — wiki page + 연결 items 모두 삭제 응답.

    wiki_page_items FK ON DELETE CASCADE 가 양방향이므로, items 삭제 시 다른
    wiki 의 sources 에서도 자동 제거된다 (사용자 명시 2026-05-27).
    """
    deleted_wiki_slug: str
    deleted_wiki_page_id: UUID
    deleted_items_count: int                # 이 wiki 에 연결됐던 items 총 개수
    affected_other_wikis_count: int          # items 삭제로 sources 가 줄어든 다른 wiki 수
    qdrant_wiki_status: int                  # 0=ok, -1=fail
    qdrant_items_status_sum: int             # 각 item 의 chunk 삭제 status 합 (0=all ok)


class WikiClassifyRequest(BaseModel):
    """POST /wiki/classify — 단일 item 또는 batch."""
    item_id: UUID | None = None
    item_ids: list[UUID] | None = None
    threshold: float = Field(default=0.5, ge=0.0, le=1.0)


class WikiClassifyItemResult(BaseModel):
    item_id: UUID
    ok: bool
    matched_count: int = 0
    new_pages_count: int = 0
    linked_page_slugs: list[str] = Field(default_factory=list)
    duration_ms: int = 0
    error: str | None = None


class WikiClassifyResponse(BaseModel):
    """POST /wiki/classify 응답 — 처리된 items 결과."""
    processed: int
    succeeded: int
    failed: int
    results: list[WikiClassifyItemResult] = Field(default_factory=list)
