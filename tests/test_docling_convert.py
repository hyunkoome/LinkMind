"""
backend.ingest.docling_convert 의 변환 결과 매핑 검증 — 가짜 DoclingDocument 으로
_doc_to_result 만 단위 테스트. Docling 실제 실행/모델 로드 없음 (cpu 카테고리).

실제 PDF→md 변환 smoke 는 무거우므로(모델 다운로드+추론) 여기 포함 X — 필요 시
tests/gpu 나 수동 실측(/tmp/test_docling_cpu.py)으로.
"""

from __future__ import annotations

import io

from PIL import Image

from backend.ingest.docling_convert import DoclingDoc, _doc_to_result


class _FakePicture:
    def __init__(self, img, caption):
        self._img = img
        self._caption = caption

    def get_image(self, doc):
        return self._img

    def caption_text(self, doc):
        return self._caption


class _FakeDoc:
    """DoclingDocument 의 _doc_to_result 가 쓰는 부분만 흉내."""

    def __init__(self, markdown, pictures, tables, pages):
        self._markdown = markdown
        self.pictures = pictures
        self.tables = tables
        self.pages = pages

    def export_to_markdown(self):
        return self._markdown


def _img(w, h):
    return Image.new("RGB", (w, h), (200, 100, 50))


def test_doc_to_result_extracts_markdown_figures_tables():
    doc = _FakeDoc(
        markdown="# Title\n\nbody text",
        pictures=[
            _FakePicture(_img(442, 642), "Figure 1: The Transformer - model architecture."),
            _FakePicture(_img(241, 335), "Figure 2: Attention."),
        ],
        tables=[object(), object(), object()],
        pages=[object()] * 15,
    )
    res = _doc_to_result(doc)
    assert isinstance(res, DoclingDoc)
    assert res.markdown.startswith("# Title")
    assert res.num_tables == 3
    assert res.num_pages == 15
    assert len(res.figures) == 2
    f0 = res.figures[0]
    assert f0.ext == "png"
    assert (f0.width, f0.height) == (442, 642)
    assert f0.caption.startswith("Figure 1")
    # PNG bytes 가 실제 디코드 가능해야 한다 (caller 가 save_bytes 로 그대로 저장).
    Image.open(io.BytesIO(f0.image_bytes)).verify()


def test_doc_to_result_skips_pictures_without_image():
    doc = _FakeDoc(
        markdown="x",
        pictures=[_FakePicture(None, "no image"), _FakePicture(_img(100, 100), None)],
        tables=[],
        pages=None,
    )
    res = _doc_to_result(doc)
    # 이미지 없는 figure 는 skip, caption 없는 건 None 으로 보존.
    assert len(res.figures) == 1
    assert res.figures[0].caption is None
    assert res.num_pages is None


def test_doc_to_result_handles_caption_exception():
    class _BadCaption(_FakePicture):
        def caption_text(self, doc):
            raise RuntimeError("docling internal")

    doc = _FakeDoc("x", [_BadCaption(_img(50, 50), None)], [], None)
    res = _doc_to_result(doc)
    # caption 추출이 터져도 figure 자체는 살린다 (caption=None).
    assert len(res.figures) == 1
    assert res.figures[0].caption is None
