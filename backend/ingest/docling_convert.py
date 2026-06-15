"""
backend.ingest.docling_convert — Docling 으로 문서(PDF/DOCX/PPTX/HTML)를 구조화된
markdown + figure 이미지 + table 로 변환.

설계 (2026-06-04):
  - 기존 pypdf/pymupdf 텍스트 추출보다 풍부한 markdown (섹션/표/수식 구조 보존).
  - figure 이미지 + caption 을 함께 추출 → attachments(role='figure') 로 저장,
    caption 은 2단계 figure 설명(VLM)의 입력. (§1 sVLL 멀티모달 학습 데이터)
  - **CPU 실행이 기본** (device='cpu'). 이유: GPU 는 vLLM(Gemma-26B) 전용 —
    Docling layout/table 모델을 GPU 에 올리면 OOM (RTX 4090 24GB 경합, 실측됨).
    CPU 로 논문 1편 ~48s, on-demand/batch 라 충분히 실용적. figure '설명'(VLM)만
    GPU 가 필요하고 그건 2단계로 분리.
  - docling import 는 무겁고 torch 를 로드하므로 **lazy import** (함수 안). backend
    startup/일반 ingest 경로에 영향 X. 변환은 blocking 이라 asyncio.to_thread.

§2 raw-first: 이 모듈은 '추출'(분석)이다. 원본 파일 bytes 는 caller 가 storage 에
무손실 보존하고, 여기서 만든 markdown 은 raw_content(재생성 가능한 추출물)로 쓴다.
"""

from __future__ import annotations

import asyncio
import io
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.utils.text import repair_surrogates

logger = logging.getLogger("linkmind.ingest.docling")

# Docling InputFormat 으로 매핑 가능한 확장자 (guess_format 결과와 정렬).
# 그 외(txt/md/image)는 Docling 을 거치지 않고 기존 경로 유지.
DOCLING_FORMATS = {"pdf", "docx", "pptx", "html"}


@dataclass
class DoclingFigure:
    """추출된 figure 한 장 — caller 가 save_bytes + insert_attachment(role='figure')."""
    image_bytes: bytes
    ext: str                       # 'png'
    width: int
    height: int
    caption: str | None = None     # Docling 이 figure 에 연결한 caption ("Figure 1: ...")


@dataclass
class DoclingDoc:
    """Docling 변환 결과 — markdown + figures + 메타."""
    markdown: str
    figures: list[DoclingFigure] = field(default_factory=list)
    num_tables: int = 0
    num_pages: int | None = None


# DocumentConverter 는 모델 로드가 비싸므로 (device, do_ocr, scale) 조합당 1회 생성 후 캐시.
_converter_cache: dict[tuple[str, bool, float], Any] = {}


def _get_converter(device: str, do_ocr: bool, images_scale: float):
    """DocumentConverter 를 lazy import + 캐시. PDF 는 figure/OCR 옵션 적용,
    DOCX/PPTX/HTML 은 native 파이프라인(옵션 무관)."""
    key = (device, do_ocr, images_scale)
    cached = _converter_cache.get(key)
    if cached is not None:
        return cached

    # ── lazy import (무거움) ──
    from docling.datamodel.accelerator_options import AcceleratorOptions
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    pdf_opts = PdfPipelineOptions()
    pdf_opts.generate_picture_images = True       # figure 이미지 추출
    pdf_opts.images_scale = images_scale          # 2.0 = 고해상도 (학습 데이터 품질)
    pdf_opts.do_ocr = do_ocr                       # 디지털 PDF 는 False (빠름)
    pdf_opts.do_picture_description = False        # figure '설명'(VLM)은 2단계(GPU)
    pdf_opts.accelerator_options = AcceleratorOptions(device=device)

    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pdf_opts)},
    )
    _converter_cache[key] = converter
    return converter


def _doc_to_result(doc: Any) -> DoclingDoc:
    """DoclingDocument → DoclingDoc (markdown + figures + 메타).

    Docling 객체 의존을 한 곳에 모아 테스트(가짜 doc)도 쉽게. figure 는 PIL → PNG bytes.
    """
    # Docling 이 수학 볼드/이탤릭(astral) 문자를 split surrogate 로 흘려보내는 경우가
    # 있어 그대로 두면 sha256_text/asyncpg 의 utf-8 인코딩에서 ingest 가 죽는다.
    # 추출 경계에서 한 번 복원해 downstream(저장/해싱/임베딩/writer)을 전부 안전하게.
    markdown = repair_surrogates(doc.export_to_markdown())
    figures: list[DoclingFigure] = []
    for pic in getattr(doc, "pictures", []) or []:
        try:
            img = pic.get_image(doc)
        except Exception:
            img = None
        if img is None:
            continue                                 # 이미지 없는 placeholder skip
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        caption = None
        try:
            caption = (pic.caption_text(doc) or "").strip() or None
            if caption is not None:
                caption = repair_surrogates(caption)
        except Exception:
            caption = None
        figures.append(DoclingFigure(
            image_bytes=buf.getvalue(), ext="png",
            width=int(img.width), height=int(img.height), caption=caption,
        ))
    num_pages = None
    try:
        num_pages = len(doc.pages) if getattr(doc, "pages", None) else None
    except Exception:
        num_pages = None
    return DoclingDoc(
        markdown=markdown, figures=figures,
        num_tables=len(getattr(doc, "tables", []) or []), num_pages=num_pages,
    )


def _convert_sync(
    source: str, *, device: str, do_ocr: bool, images_scale: float,
) -> DoclingDoc:
    """blocking 변환 — asyncio.to_thread 로 감싸 호출."""
    converter = _get_converter(device, do_ocr, images_scale)
    result = converter.convert(source)
    return _doc_to_result(result.document)


async def convert_document(
    source: str | Path,
    *,
    device: str = "cpu",
    do_ocr: bool = False,
    images_scale: float = 2.0,
) -> DoclingDoc:
    """문서(로컬 경로 또는 URL)를 Docling 으로 변환. blocking 이라 to_thread.

    device: 'cpu'(기본, GPU 는 vLLM 전용) | 'cuda'(figure 설명 등 GPU 단계).
    do_ocr: 스캔 PDF 면 True. 디지털 PDF/논문은 False(빠름).
    """
    return await asyncio.to_thread(
        _convert_sync, str(source),
        device=device, do_ocr=do_ocr, images_scale=images_scale,
    )


async def save_docling_figures(session, *, item_id, figures: list[DoclingFigure]) -> int:
    """Docling 이 추출한 figure 들을 storage 저장 + attachments(role='figure') INSERT.

    pdf 모듈의 _save_pdf_figures 와 동일 패턴 (save_bytes SHA-256 dedup → insert_attachment)
    이되 caption 이 Docling 이 figure 에 연결한 실제 캡션("Figure 1: ...")이다 — 2단계
    figure 설명(VLM)의 입력. 반환: 새로 저장된 figure 수 (중복은 ON CONFLICT 로 skip).
    """
    from backend.db.repository import insert_attachment
    from backend.storage.local import save_bytes

    saved = 0
    for idx, fig in enumerate(figures):
        if not fig.image_bytes:
            continue
        try:
            fp, fh, fsize = save_bytes(fig.image_bytes)
        except Exception as e:  # noqa: BLE001
            logger.warning("docling figure %d storage 저장 실패: %s", idx, e)
            continue
        att_id = await insert_attachment(
            session,
            item_id=item_id,
            file_path=fp,
            file_hash=fh,
            file_size=fsize,
            mime_type=f"image/{fig.ext}",
            role="figure",
            width=fig.width,
            height=fig.height,
            caption=fig.caption,
        )
        if att_id is not None:
            saved += 1
    return saved
