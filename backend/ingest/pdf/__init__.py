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
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from backend.db.connection import get_engine
from backend.db.repository import find_item_by_hash, insert_attachment, insert_item
from backend.ingest.url import (
    ExtractedDoc,
    _embed_and_index,
    _generate_and_save_summary,
    auto_link_topics,
    refresh_existing_item_analysis,
)
from backend.storage.local import save_bytes
from backend.utils.external_ids import extract_external_ids
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


# Abstract 라벨 — "Abstract", "ABSTRACT", "Abstract:", "Abstract.", "Abstract—" 등.
# 시작 anchor (^ or \n) 로 본문 중간의 단어 "abstract" 매칭은 피한다.
_ABSTRACT_LABEL_RE = re.compile(
    r"(?:^|\n)\s*abstract\s*[\.\:\-—–]*\s*",
    re.IGNORECASE,
)

# Abstract 끝 신호 — 다음 섹션 시작 또는 단락 break.
# PDF 추출이 종종 대문자 제목 글자 사이에 공백을 끼워넣음 ("I NTRODUCTION", "S UMMARY") —
# `\s?` 를 사이사이에 넣어 그 케이스도 잡는다.
_INTRO_LOOSE = r"i\s?n\s?t\s?r\s?o\s?d\s?u\s?c\s?t\s?i\s?o\s?n"

# `(?P<sec>...)` 그룹 — 라벨 기반 1차에서는 `\n\s*\n` 도 포함, 라벨 없는 fallback 에서는
# 명시적 섹션 헤더만 사용 (빈 줄은 author/소속 단락 뒤에도 흔해 fallback 노이즈).
_SECTION_HEADERS = (
    rf"(?:^|\n)\s*(?:[1IⅠ]\.?|section\s+[1IⅠ])\s+{_INTRO_LOOSE}"  # "1. Introduction" / "I. Introduction"
    rf"|(?:^|\n)\s*{_INTRO_LOOSE}\b"
    r"|(?:^|\n)\s*key\s*words?\b"
    r"|(?:^|\n)\s*index\s+terms\b"
    r"|(?:^|\n)\s*ccs\s+concepts\b"
    r"|(?:^|\n)\s*categories\s+and\s+subject\s+descriptors"
    r"|(?:^|\n)\s*supplementary\s+material"
)
# 1차 (라벨 다음 본문이 어디서 끝나는지) 용 — 빈 줄도 종결로 인정.
_ABSTRACT_END_RE = re.compile(r"\n\s*\n|" + _SECTION_HEADERS, re.IGNORECASE)
# 2차 fallback 용 — 빈 줄 제외, 섹션 헤더만.
_SECTION_ONLY_RE = re.compile(_SECTION_HEADERS, re.IGNORECASE)


# PDF Title metadata 의 자동 생성 placeholder — paper 제목이 아님.
_PLACEHOLDER_TITLE_RE = re.compile(
    r"^(microsoft\s*word|untitled|paper|manuscript|temp|test|draft|"
    r"document\d*|.*\.docx?$|.*\.tex$|.*\.indd$|.*\.fm$)",
    re.IGNORECASE,
)


def _is_placeholder_title(t: str) -> bool:
    """PDF metadata Title 이 실제 paper 제목인지, 자동 생성된 placeholder 인지."""
    if not t or len(t) < 4:
        return True
    if _PLACEHOLDER_TITLE_RE.search(t.strip()):
        return True
    return False


def _extract_pdf_title(info: dict, body: str) -> str | None:
    """PDF Title 추출. 우선순위:

    1) PDF metadata 의 Title (단 'Microsoft Word - foo.docx' 같은 placeholder 거름)
    2) body 첫 ~30 줄 안에서 첫 의미있는 텍스트 (보통 PDF 첫 페이지의 paper title)
       — 'Abstract' / 'arxiv:' 같은 마커 만나기 전까지, 너무 짧거나 숫자만 인 줄 skip
    3) None — 호출자가 fallback 처리 (예: 요약 후 LLM 으로 보강)
    """
    raw = (info.get("Title") or info.get("/Title") or "").strip()
    if raw and not _is_placeholder_title(raw):
        return raw[:300]

    # body 첫 부분에서 paper title 후보 — author/affiliation/abstract 마커 앞까지
    for raw_line in body.splitlines()[:30]:
        line = raw_line.strip()
        if not line or len(line) < 8:
            continue
        # 'Abstract', 'arxiv:', 'doi:', '1. Introduction' 같은 마커 만나면 stop
        if re.match(r"^(abstract|arxiv:|doi:|keywords?:|1\.\s+intro)",
                    line, re.IGNORECASE):
            break
        # 너무 긴 줄 (paper title 은 보통 < 200자)
        if len(line) > 250:
            continue
        # 숫자/page 번호만
        if re.match(r"^[\d\.\s/-]+$", line):
            continue
        # affiliation / 이메일 / DOI prefix 줄
        if re.match(r"^(.*@.*|.*affiliation|.*department|.*university)",
                    line, re.IGNORECASE):
            continue
        # 'This paper has been accepted ...' 같은 헤더 (FAST-LIVO2 가 'IEEE' 헤더로 시작)
        if re.match(r"^(this paper|©|copyright|ieee|acm|preprint)",
                    line, re.IGNORECASE):
            continue
        return line[:300]
    return None


def _detect_abstract(text_in: str) -> str | None:
    """본문 앞 8000자 안에서 abstract 추출. 라벨 우선 → 다음 섹션 직전 단락 fallback.

    잡는 케이스:
    - "Abstract—..." (em-dash, IEEE/ICRA 스타일)
    - "Abstract: ..." (학회/저널)
    - "ABSTRACT\n..." (라벨 한 줄, 다음 줄부터 본문)
    - 라벨 없이 author/affiliation 다음 첫 단락 (워크샵 페이퍼 일부)

    너무 짧은 (<100자) 후보는 reject — 본문 fallback 으로 넘김 (요약 입력 안정성).
    """
    head = text_in[:8000]

    # 1차: 라벨 기반 — "Abstract" 다음의 본문을 다음 섹션/단락까지.
    m_label = _ABSTRACT_LABEL_RE.search(head)
    if m_label:
        body = head[m_label.end():]
        m_end = _ABSTRACT_END_RE.search(body)
        end = m_end.start() if m_end else min(5000, len(body))
        candidate = re.sub(r"\s+", " ", body[:end]).strip()
        if 100 <= len(candidate) <= 6000:
            return candidate
        # 길이 미달이면 그대로 fallback 으로 진행 (라벨이 misleading 인 경우 대비)

    # 2차: 라벨 없음 — "Introduction" 등 명시적 섹션 헤더 직전 단락이 abstract 후보.
    # 빈 줄(\n\s*\n) 만 보면 저자/소속 단락이 잘못 잡혀서 fallback 이 너무 앞에서 끊김.
    m_sec = _SECTION_ONLY_RE.search(head)
    if m_sec and m_sec.start() >= 200:
        before = head[: m_sec.start()]
        for para in reversed(re.split(r"\n\s*\n", before)):
            candidate = re.sub(r"\s+", " ", para).strip()
            if 200 <= len(candidate) <= 6000 and len(candidate.split()) >= 30:
                return candidate

    return None


async def _insert_pdf_attachment(
    session: AsyncSession,
    *,
    item_id: UUID,
    file_path: str,
    file_hash: str,
    file_size: int,
) -> UUID | None:
    return await insert_attachment(
        session,
        item_id=item_id,
        file_path=file_path,
        file_hash=file_hash,
        file_size=file_size,
        mime_type="application/pdf",
        role="attachment",
    )


# pymupdf 가 추출하는 image ext 와 MIME 매핑.
_EXT_MIME = {
    "png": "image/png", "jpeg": "image/jpeg", "jpg": "image/jpeg",
    "tiff": "image/tiff", "tif": "image/tiff", "gif": "image/gif",
    "webp": "image/webp", "bmp": "image/bmp", "jp2": "image/jp2",
    "jbig2": "image/jbig2",
}

# figure 로 간주하는 최소 가로/세로 (px). 그 미만은 페이지 마진의 로고/장식 등
# 비-figure 일 가능성이 커서 제외 (학습 데이터 노이즈 방지).
_FIGURE_MIN_DIM = 200


def _extract_pdf_figures(data: bytes) -> list[dict[str, Any]]:
    """pymupdf 로 PDF figure 이미지 추출. xref 기준 dedup.

    반환 항목 dict 구조: {bytes, ext, width, height, page_indices: list[int]}.
    pypdf 는 image 추출이 약해서 fallback 없이 pymupdf 만 사용.
    """
    try:
        import fitz  # pymupdf
    except Exception as e:  # noqa: BLE001
        logger.warning("pymupdf import 실패 (figure 추출 skip): %s", e)
        return []

    pdf_doc = fitz.open(stream=data, filetype="pdf")
    by_xref: dict[int, dict[str, Any]] = {}
    try:
        for pno, page in enumerate(pdf_doc):
            try:
                imgs = page.get_images(full=True)
            except Exception as e:  # noqa: BLE001
                logger.warning("PDF page %d get_images 실패: %s", pno, e)
                continue
            for img in imgs:
                xref = img[0]
                if xref in by_xref:
                    by_xref[xref]["page_indices"].append(pno)
                    continue
                try:
                    ext = pdf_doc.extract_image(xref)
                except Exception as e:  # noqa: BLE001
                    logger.warning("PDF figure xref=%d 추출 실패: %s", xref, e)
                    continue
                width = int(ext.get("width") or 0)
                height = int(ext.get("height") or 0)
                if width < _FIGURE_MIN_DIM or height < _FIGURE_MIN_DIM:
                    continue
                by_xref[xref] = {
                    "bytes": ext.get("image", b""),
                    "ext": (ext.get("ext") or "png").lower(),
                    "width": width,
                    "height": height,
                    "page_indices": [pno],
                }
    finally:
        pdf_doc.close()
    return list(by_xref.values())


async def _save_pdf_figures(
    session: AsyncSession, *, item_id: UUID, data: bytes,
) -> int:
    """PDF 본문 bytes 에서 figure 추출 + storage 저장 + attachments INSERT.

    반환은 새로 저장된 figure 수 (이미 같은 file_hash 가 있으면 ON CONFLICT 로 skip
    돼서 count 에 안 들어감 — None 반환).
    """
    figures = _extract_pdf_figures(data)
    saved = 0
    for idx, fig in enumerate(figures):
        if not fig["bytes"]:
            continue
        try:
            fp, fh, fsize = save_bytes(fig["bytes"])
        except Exception as e:  # noqa: BLE001
            logger.warning("figure %d storage 저장 실패: %s", idx, e)
            continue
        att_id = await insert_attachment(
            session,
            item_id=item_id,
            file_path=fp,
            file_hash=fh,
            file_size=fsize,
            mime_type=_EXT_MIME.get(fig["ext"], "application/octet-stream"),
            role="figure",
            width=fig["width"],
            height=fig["height"],
            caption=f"page {min(fig['page_indices']) + 1} (xref reused on {len(fig['page_indices'])} pages)"
            if len(fig["page_indices"]) > 1 else f"page {fig['page_indices'][0] + 1}",
        )
        if att_id is not None:
            saved += 1
    return saved


async def ingest_pdf(
    src: str | Path, *,
    analyze_now: bool = True,
    force: bool = False,
    caption: str | None = None,
) -> dict[str, Any]:
    """PDF 한 건 처리. src 는 로컬 파일 경로 또는 https URL.

    force=True 면 동일 hash 기존 item 의 summary/tags/source_metadata 만 재계산
    (raw_content/chunks/attachments 는 그대로).

    caption: 텔레그램 등에서 PDF URL 과 같이 온 사용자 메모. user_notes 에 append
    (idempotent — 같은 caption 두 번이면 dedup. Phase 2.5 wave-3 정책).
    """
    data, external_url = await _load_pdf_bytes(src)
    file_path, file_hash, file_size = save_bytes(data)
    # 외부 URL 이 있으면 그대로 (출처 추적), 없으면 우리 files endpoint 로 — 브라우저에서
    # 클릭하면 inline PDF viewer 가 뜸. path-only 로 저장하면 UI 가 API_BASE 와 결합.
    source_url = external_url or f"/files/{file_hash}"
    body, pdf_meta = _extract_pdf_text(data)
    if not body or len(body.strip()) < 50:
        raise ValueError(f"PDF 텍스트 추출 실패 또는 본문이 너무 짧습니다: {src}")

    info = pdf_meta.get("info", {}) or {}
    title = _extract_pdf_title(info, body)
    abstract = _detect_abstract(body)

    # PDF 본문에서 arxiv id / DOI / GitHub repo / arxiv link 등 추출 — topic auto-link 단서.
    ext_ids = extract_external_ids(url=external_url, text=body[:20000])

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
            # 옛 item 에 figure 가 없을 수 있으니 force 일 때 backfill 시도 — analyze_now
            # 일 때만 (단순 dedup hit 에서 figure 추출 비용을 강요하지 않음).
            figures_saved_existing = 0
            if force and analyze_now:
                figures_saved_existing = await _save_pdf_figures(
                    session, item_id=existing, data=data,
                )
            # 새 caption 이면 user_notes append (dedup 에서도 사용자 메모 보존)
            if caption and caption.strip():
                from backend.db.repository import append_item_user_notes
                await append_item_user_notes(
                    session, item_id=existing, new_note=caption.strip(),
                )
            await session.commit()
            if not force:
                return {"item_id": str(existing), "created": False, "chunks_indexed": 0}
            refreshed = await refresh_existing_item_analysis(
                session,
                item_id=existing,
                doc=doc,
                source_metadata={
                    "file_hash": file_hash,
                    "file_size": file_size,
                    "file_path": file_path,
                    "pdf": pdf_meta,
                    "external_ids": [
                        {"kind": x.kind, "value": x.value} for x in ext_ids
                    ],
                },
            )
            await auto_link_topics(
                session, item_id=existing, source_type="pdf",
                title=doc.title, ids=ext_ids,
            )
            await session.commit()
            return {
                "item_id": str(existing),
                "created": False,
                "refreshed": True,
                "chunks_indexed": 0,
                "figures_saved": figures_saved_existing,
                "summary_generated": refreshed["summary"] is not None,
                "tags": refreshed["tags"],
                "title": title,
                "file_path": file_path,
                "file_hash": file_hash,
            }

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
                "external_ids": [
                    {"kind": x.kind, "value": x.value} for x in ext_ids
                ],
            },
            title=title,
            source_created_at=None,
        )
        await _insert_pdf_attachment(
            session, item_id=item_id, file_path=file_path,
            file_hash=file_hash, file_size=file_size,
        )
        # PDF 의 arxiv_id / DOI / paper-link 단서로 자동 topic 매핑.
        await auto_link_topics(
            session, item_id=item_id, source_type="pdf",
            title=title, ids=ext_ids,
        )
        # caption (텔레그램에서 PDF 와 같이 온 사용자 메모) → user_notes
        if caption and caption.strip():
            from backend.db.repository import append_item_user_notes
            await append_item_user_notes(
                session, item_id=item_id, new_note=caption.strip(),
            )
        await session.commit()

        chunks_indexed = 0
        figures_saved = 0
        summary_text: str | None = None
        tags: list[str] = []
        if analyze_now:
            chunks_indexed = await _embed_and_index(session, item_id=item_id, text=body)
            # figure 추출은 summary 보다 빠르므로 chunks 다음 / summary 이전에 배치.
            figures_saved = await _save_pdf_figures(
                session, item_id=item_id, data=data,
            )
            await session.commit()
            summary_text, tags = await _generate_and_save_summary(
                session, item_id=item_id, doc=doc,
            )

        return {
            "item_id": str(item_id),
            "created": True,
            "chunks_indexed": chunks_indexed,
            "figures_saved": figures_saved,
            "summary_generated": summary_text is not None,
            "tags": tags,
            "title": title,
            "file_path": file_path,
            "file_hash": file_hash,
        }
