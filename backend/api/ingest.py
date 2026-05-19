"""
POST /ingest — 외부 client(OpenClaw extension, 스크립트 등)가 자료를 LinkMind에 넣는 엔드포인트.

흐름:
  1) raw_content hash 계산
  2) 동일 hash 존재하면 skip (idempotent)
  3) items 에 INSERT
  4) (analyze_now=True) chunking → embedding → chunks INSERT → Qdrant upsert
  5) (옵션) AI 요약/태깅은 추후 background task로 분리 (Phase 2)
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.connection import get_session
from backend.db.repository import (
    find_item_by_hash,
    insert_chunks,
    insert_item,
)
from backend.embedding.factory import get_embedding_provider
from backend.embedding.qdrant_store import ensure_collection, upsert_chunks
from backend.schemas.models import IngestRequest, IngestResponse
from backend.utils.chunking import chunk_text
from backend.utils.hashing import sha256_text

logger = logging.getLogger(__name__)
router = APIRouter()


class UrlIngestRequest(BaseModel):
    url: str = Field(..., min_length=1)
    analyze_now: bool = True
    force: bool = Field(
        default=False,
        description=(
            "True 면 동일 hash 의 기존 item 도 skip 하지 않고 summary/tags/source_metadata "
            "를 재계산. raw_content/chunks 는 그대로 두므로 비용은 LLM 요약 1회분."
        ),
    )


class UrlIngestResponse(BaseModel):
    item_id: str
    created: bool
    refreshed: bool = False
    chunks_indexed: int = 0
    figures_saved: int = 0       # PDF only — pymupdf get_images() 로 추출한 figure 수
    thumbnail_saved: int = 0     # YouTube only — 영상/playlist 썸네일 attachments 수 (0/1)
    summary_generated: bool = False
    tags: list[str] = Field(default_factory=list)
    title: str | None = None


def _wrap_result(result: dict[str, Any]) -> "UrlIngestResponse":
    return UrlIngestResponse(**{k: result.get(k) for k in (
        "item_id", "created", "refreshed", "chunks_indexed",
        "figures_saved", "thumbnail_saved",
        "summary_generated", "tags", "title",
    ) if k in result})


def _classify_url(url: str) -> str:
    """URL host 로 source 종류 추정. 'youtube' | 'github' | 'pdf' | 'url'.

    pdf 판정:
    - path 가 `.pdf` 로 끝남 (정통 케이스)
    - path 안에 `/pdf/` 세그먼트 (arxiv.org/pdf/2106.14490, openreview/pdf?id=...,
      conference site 의 /pdf/...). Slack backfill 에서 발견된 패턴 — 기존엔
      url 로 잘못 라우팅돼서 readability fallback 만 도는 placeholder 들이 생김.
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host.endswith("youtube.com") or host == "youtu.be":
        return "youtube"
    if host == "github.com" or host == "www.github.com":
        return "github"
    path_lower = parsed.path.lower()
    if path_lower.endswith(".pdf") or "/pdf/" in path_lower:
        return "pdf"
    return "url"


@router.post("/url", response_model=UrlIngestResponse)
async def ingest_url_endpoint(payload: UrlIngestRequest) -> UrlIngestResponse:
    """URL 한 줄 ingest — 일반 웹 페이지/논문 abstract. 본격 흐름은 backend.ingest.url."""
    from backend.ingest.url import ingest_url
    try:
        result = await ingest_url(
            payload.url, analyze_now=payload.analyze_now, force=payload.force,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("URL ingest 실패: %s", payload.url)
        raise HTTPException(status_code=500, detail=f"URL ingest 실패: {e!s}") from e
    return _wrap_result(result)


@router.post("/youtube", response_model=UrlIngestResponse)
async def ingest_youtube_endpoint(payload: UrlIngestRequest) -> UrlIngestResponse:
    from backend.ingest.youtube import ingest_youtube
    try:
        result = await ingest_youtube(
            payload.url, analyze_now=payload.analyze_now, force=payload.force,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("YouTube ingest 실패: %s", payload.url)
        raise HTTPException(status_code=500, detail=f"YouTube ingest 실패: {e!s}") from e
    return _wrap_result(result)


@router.post("/github", response_model=UrlIngestResponse)
async def ingest_github_endpoint(payload: UrlIngestRequest) -> UrlIngestResponse:
    from backend.ingest.github import ingest_github
    try:
        result = await ingest_github(
            payload.url, analyze_now=payload.analyze_now, force=payload.force,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("GitHub ingest 실패: %s", payload.url)
        raise HTTPException(status_code=500, detail=f"GitHub ingest 실패: {e!s}") from e
    return _wrap_result(result)


@router.post("/pdf", response_model=UrlIngestResponse)
async def ingest_pdf_endpoint(payload: UrlIngestRequest) -> UrlIngestResponse:
    """PDF URL ingest. multipart 파일 업로드는 /ingest/pdf/upload 사용."""
    from backend.ingest.pdf import ingest_pdf
    try:
        result = await ingest_pdf(
            payload.url, analyze_now=payload.analyze_now, force=payload.force,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("PDF ingest 실패: %s", payload.url)
        raise HTTPException(status_code=500, detail=f"PDF ingest 실패: {e!s}") from e
    return _wrap_result(result)


@router.post("/pdf/upload", response_model=UrlIngestResponse)
async def ingest_pdf_upload(
    file: UploadFile = File(...),
    analyze_now: bool = True,
    force: bool = False,
) -> UrlIngestResponse:
    """multipart PDF 파일 업로드 ingest. tempfile 로 받아 ingest_pdf 호출."""
    from backend.ingest.pdf import ingest_pdf
    if not (file.filename or "").lower().endswith(".pdf"):
        # MIME 만 보고 신뢰하긴 어려워 확장자도 확인.
        if file.content_type not in ("application/pdf", "application/x-pdf"):
            raise HTTPException(400, "PDF 파일만 허용됩니다")
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(await file.read())
            tmp_path = Path(tmp.name)
        try:
            result = await ingest_pdf(tmp_path, analyze_now=analyze_now, force=force)
        finally:
            tmp_path.unlink(missing_ok=True)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("PDF upload ingest 실패: %s", file.filename)
        raise HTTPException(status_code=500, detail=f"PDF ingest 실패: {e!s}") from e
    return _wrap_result(result)


@router.post("/auto", response_model=UrlIngestResponse)
async def ingest_auto(payload: UrlIngestRequest) -> UrlIngestResponse:
    """URL host 로 자동 분류 후 해당 ingester 호출.

    분류 결과:
      - youtube.com / youtu.be     → youtube
      - github.com                 → github
      - 확장자 *.pdf               → pdf (URL)
      - 그 외                      → url (일반 페이지)
    """
    kind = _classify_url(payload.url)
    if kind == "youtube":
        return await ingest_youtube_endpoint(payload)
    if kind == "github":
        return await ingest_github_endpoint(payload)
    if kind == "pdf":
        return await ingest_pdf_endpoint(payload)
    return await ingest_url_endpoint(payload)


@router.post("", response_model=IngestResponse)
async def ingest(
    payload: IngestRequest,
    session: AsyncSession = Depends(get_session),
) -> IngestResponse:
    content_hash = sha256_text(payload.raw_content)

    # 1) idempotent 체크
    existing = await find_item_by_hash(
        session, source_type=payload.source_type, content_hash=content_hash
    )
    if existing is not None:
        return IngestResponse(item_id=existing, created=False, chunks_indexed=0)

    # 2) item 저장 (raw-first)
    item_id = await insert_item(
        session,
        source_type=payload.source_type,
        raw_content=payload.raw_content,
        raw_content_hash=content_hash,
        source_id=payload.source_id,
        source_url=str(payload.source_url) if payload.source_url else None,
        source_metadata=payload.source_metadata,
        title=payload.title,
        source_created_at=payload.source_created_at,
    )
    await session.commit()

    # 3) (옵션) 즉시 임베딩
    chunks_indexed = 0
    if payload.analyze_now:
        try:
            chunks_indexed = await _embed_and_index(
                session, item_id=item_id, raw_content=payload.raw_content,
                source_type=payload.source_type,
            )
        except Exception as e:                   # noqa: BLE001 — embedding 실패해도 raw는 보존됨
            logger.exception("embedding/index 실패 (item_id=%s): %s", item_id, e)
            # raw_content는 이미 저장됐으므로 나중에 재처리 가능.
            raise HTTPException(status_code=500, detail=f"embedding 실패: {e!s}") from e

    return IngestResponse(item_id=item_id, created=True, chunks_indexed=chunks_indexed)


async def _embed_and_index(
    session: AsyncSession,
    *,
    item_id: Any,
    raw_content: str,
    source_type: str,
) -> int:
    """chunking → embedding → DB chunks + Qdrant upsert."""
    embedder = get_embedding_provider()
    await ensure_collection(dim=embedder.dim)

    chunks = chunk_text(raw_content)
    if not chunks:
        return 0

    emb = await embedder.embed(chunks)
    chunk_ids = await insert_chunks(
        session,
        item_id=item_id,
        chunks=chunks,
        embedding_model=embedder.model,
        embedding_dim=embedder.dim,
    )
    await session.commit()

    # Qdrant payload (검색 필터링용 메타만 — 본문은 Postgres에서 join)
    payloads = [
        {
            "item_id": str(item_id),
            "chunk_index": idx,
            "source_type": source_type,
            "snippet": ctext[:300],
        }
        for idx, ctext in enumerate(chunks)
    ]
    await upsert_chunks(
        chunk_ids=[str(cid) for cid in chunk_ids],
        vectors=emb.vectors,
        payloads=payloads,
    )
    return len(chunks)
