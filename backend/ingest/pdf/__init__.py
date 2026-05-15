"""
PDF ingester.

흐름
----
1. 입력: 로컬 파일 path 또는 https URL.
2. 원본 PDF 보존 (필수, loss-less 원칙):
   - storage.save_bytes / save_file 로 file_hash 기반 경로에 저장
   - attachments 테이블에 (item_id, file_path, file_hash, mime, file_size) INSERT
3. 텍스트 추출: pypdf 우선, 실패 시 pymupdf (fitz) fallback.
4. abstract: 첫 페이지에서 "Abstract" 섹션 추출 (논문 PDF 의 경우 정확도 높음).
5. items 저장 후 url ingest 와 동일한 helper 로 임베딩 + 요약 + 해시태그.

PDF 자체는 절대 리사이즈/압축하지 않음 (CLAUDE.md NEVER 목록).
"""

from __future__ import annotations

import io
import logging
import re
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from backend.db.connection import get_engine
from backend.db.repository import find_item_by_hash, insert_item
from backend.ingest.url import (
    ExtractedDoc,
    _embed_and_index,
    _generate_and_save_summary,
)
from backend.storage.local import save_bytes
from backend.utils.hashing import sha256_text

logger = logging.getLogger(__name__)


async def _load_pdf_bytes(src: str | Path) -> tuple[bytes, str | None]:
    """입력 소스에서 PDF 바이트 + 외부 source_url 반환.

    로컬 파일/tempfile 인 경우 외부 URL 이 없으므로 None — 호출자가 file_hash 기반
    `/files/{hash}` 로 source_url 을 채운다 (브라우저에서 inline 표시 가능).
    """
    if isinstance(src, Path) or (
        isinstance(src, str) and not str(src).startswith(("http://", "https://"))
    ):
        p = Path(src)
        if not p.exists():
            raise ValueError(f"PDF 파일이 없습니다: {p}")
        data = p.read_bytes()
        return data, None
    async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
        r = await client.get(
            str(src),
            headers={"User-Agent": "LinkMind/0.1 (+https://github.com/Real2Real-AI/LinkMind)"},
        )
        r.raise_for_status()
        return r.content, str(src)


def _extract_text_pypdf(data: bytes) -> tuple[str, dict[str, Any]]:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    pages = []
    for i, page in enumerate(reader.pages):
        try:
            pages.append(page.extract_text() or "")
        except Exception as e:  # noqa: BLE001
            logger.warning("pypdf 페이지 %d 추출 실패: %s", i, e)
            pages.append("")
    meta = {
        "num_pages": len(reader.pages),
        "info": {str(k): str(v) for k, v in (reader.metadata or {}).items()},
    }
    return "\n\n".join(pages).strip(), meta


def _extract_text_pymupdf(data: bytes) -> tuple[str, dict[str, Any]]:
    import fitz  # pymupdf
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        pages = [page.get_text() for page in doc]
        meta = {"num_pages": doc.page_count, "info": dict(doc.metadata or {})}
    finally:
        doc.close()
    return "\n\n".join(p.strip() for p in pages).strip(), meta


_NUL_RE = re.compile(r"\x00")


def _sanitize_text(s: str) -> str:
    """Postgres TEXT 컬럼에 넣기 전 NUL byte 제거.

    PDF 텍스트 추출 시 가끔 NUL(0x00) 이 섞여 들어옴 — Postgres UTF-8 이 reject.
    """
    if not s:
        return ""
    return _NUL_RE.sub("", s)


def _extract_pdf_text(data: bytes) -> tuple[str, dict[str, Any]]:
    """pypdf 1차 → pymupdf fallback. 둘 다 실패하면 빈 문자열.
    추출 결과는 _sanitize_text 로 NUL byte 제거 후 반환.
    """
    try:
        text_out, meta = _extract_text_pypdf(data)
        if text_out.strip():
            return _sanitize_text(text_out), {**meta, "extractor": "pypdf"}
    except Exception as e:  # noqa: BLE001
        logger.warning("pypdf 실패: %s", e)
    try:
        text_out, meta = _extract_text_pymupdf(data)
        return _sanitize_text(text_out), {**meta, "extractor": "pymupdf"}
    except Exception as e:  # noqa: BLE001
        logger.warning("pymupdf 실패: %s", e)
    return "", {"extractor": "none"}


_ABSTRACT_HEAD_RE = re.compile(
    r"abstract[\s\.\:\-—]+(.{200,3000}?)(?=\n\s*\n|introduction|1\.\s+introduction|keywords)",
    re.IGNORECASE | re.DOTALL,
)


def _detect_abstract(text_in: str) -> str | None:
    """본문 앞 5000자에서 'Abstract' 섹션 추출. 실패하면 None."""
    head = text_in[:5000]
    m = _ABSTRACT_HEAD_RE.search(head)
    if not m:
        return None
    abs_text = re.sub(r"\s+", " ", m.group(1)).strip()
    return abs_text if len(abs_text) >= 100 else None


async def _insert_pdf_attachment(
    session: AsyncSession,
    *,
    item_id: UUID,
    file_path: str,
    file_hash: str,
    file_size: int,
) -> UUID | None:
    res = await session.execute(
        text("""
            INSERT INTO attachments (
                item_id, file_path, mime_type, file_size, file_hash, role
            ) VALUES (
                :item_id, :file_path, :mime, :size, :hash, 'attachment'
            )
            ON CONFLICT (item_id, file_hash) DO NOTHING
            RETURNING id
        """),
        {
            "item_id": item_id,
            "file_path": file_path,
            "mime": "application/pdf",
            "size": file_size,
            "hash": file_hash,
        },
    )
    return res.scalar_one_or_none()


async def ingest_pdf(src: str | Path, *, analyze_now: bool = True) -> dict[str, Any]:
    """PDF 한 건 처리. src 는 로컬 파일 경로 또는 https URL."""
    data, external_url = await _load_pdf_bytes(src)
    file_path, file_hash, file_size = save_bytes(data)
    # 외부 URL 이 있으면 그대로 (출처 추적), 없으면 우리 files endpoint 로 — 브라우저에서
    # 클릭하면 inline PDF viewer 가 뜸. path-only 로 저장하면 UI 가 API_BASE 와 결합.
    source_url = external_url or f"/files/{file_hash}"
    body, pdf_meta = _extract_pdf_text(data)
    if not body or len(body.strip()) < 50:
        raise ValueError(f"PDF 텍스트 추출 실패 또는 본문이 너무 짧습니다: {src}")

    info = pdf_meta.get("info", {}) or {}
    title = (info.get("Title") or info.get("/Title") or "").strip() or None
    abstract = _detect_abstract(body)

    paper_keywords: list[str] = ["pdf"]
    if (info.get("Author") or info.get("/Author") or "").strip():
        paper_keywords.append("has-author-meta")

    doc = ExtractedDoc(
        body=body, title=title, abstract=abstract, paper_keywords=paper_keywords,
    )

    content_hash = sha256_text(body)
    engine = get_engine()
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        existing = await find_item_by_hash(
            session, source_type="pdf", content_hash=content_hash,
        )
        if existing is not None:
            await _insert_pdf_attachment(
                session, item_id=existing, file_path=file_path,
                file_hash=file_hash, file_size=file_size,
            )
            await session.commit()
            return {"item_id": str(existing), "created": False, "chunks_indexed": 0}

        item_id = await insert_item(
            session,
            source_type="pdf",
            raw_content=body,
            raw_content_hash=content_hash,
            source_id=file_hash,
            source_url=source_url,
            source_metadata={
                "file_hash": file_hash,
                "file_size": file_size,
                "file_path": file_path,
                "pdf": pdf_meta,
            },
            title=title,
            source_created_at=None,
        )
        await _insert_pdf_attachment(
            session, item_id=item_id, file_path=file_path,
            file_hash=file_hash, file_size=file_size,
        )
        await session.commit()

        chunks_indexed = 0
        summary_text: str | None = None
        tags: list[str] = []
        if analyze_now:
            chunks_indexed = await _embed_and_index(session, item_id=item_id, text=body)
            summary_text, tags = await _generate_and_save_summary(
                session, item_id=item_id, doc=doc,
            )

        return {
            "item_id": str(item_id),
            "created": True,
            "chunks_indexed": chunks_indexed,
            "summary_generated": summary_text is not None,
            "tags": tags,
            "title": title,
            "file_path": file_path,
            "file_hash": file_hash,
        }
