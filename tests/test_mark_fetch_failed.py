"""
tests/test_mark_fetch_failed.py
----------------------------------------------------------------------------
D12 의 fetch_error 자동 분류 job pure helper 단위 테스트.

classify_item 의 우선순위:
  extraction_failed > image_no_ocr > binary_no_extract > short_raw > None
"""
from __future__ import annotations

from backend.jobs.mark_fetch_failed import classify_item


def test_정상_자료는_None() -> None:
    """summary 있고 raw 충분 → cleanup 대상 아님."""
    result = classify_item(
        source_type="url",
        raw_content="a" * 1000,
        summary="요약 본문...",
        source_metadata={},
    )
    assert result is None


def test_정상_자료_metadata_None() -> None:
    """source_metadata None 도 안전 처리."""
    result = classify_item(
        source_type="url",
        raw_content="a" * 500,
        summary="ok",
        source_metadata=None,
    )
    assert result is None


def test_extraction_failed_marker_없으면_표준화() -> None:
    """url_only 로 들어왔던 옛 row — fetch_error string 있고 kind 없음."""
    result = classify_item(
        source_type="url",
        raw_content="[url-only fallback]\nError: 503",
        summary=None,
        source_metadata={"fetch_error": "HTTP 503"},
    )
    assert result == "extraction_failed"


def test_extraction_failed_kind_이미_있으면_None() -> None:
    """이미 kind 마킹된 row 는 재마킹 안 함 (idempotent)."""
    result = classify_item(
        source_type="url",
        raw_content="[url-only fallback]",
        summary=None,
        source_metadata={
            "fetch_error": "HTTP 503",
            "fetch_error_kind": "extraction_failed",
        },
    )
    assert result is None


def test_image_no_ocr_텔레그램_사진() -> None:
    """document 형식의 텔레그램 이미지 첨부."""
    raw = "[binary file: photo_2026-05-16.jpg, mime=image/jpeg, size=63869 bytes, format=image]"
    result = classify_item(
        source_type="document",
        raw_content=raw,
        summary=None,
        source_metadata={},
    )
    assert result == "image_no_ocr"


def test_image_no_ocr_PNG() -> None:
    raw = "[binary file: foo.png, mime=image/png, size=1024 bytes]"
    result = classify_item(
        source_type="document",
        raw_content=raw,
        summary=None,
        source_metadata={},
    )
    assert result == "image_no_ocr"


def test_binary_no_extract_이미지_아닌_바이너리() -> None:
    raw = "[binary file: archive.zip, mime=application/zip, size=1024 bytes]"
    result = classify_item(
        source_type="document",
        raw_content=raw,
        summary=None,
        source_metadata={},
    )
    assert result == "binary_no_extract"


def test_short_raw_본문_거의_빈_URL() -> None:
    """URL ingest 가 abstract/body 거의 못 가져온 케이스 — 옛 row."""
    result = classify_item(
        source_type="url",
        raw_content="짧은 본문...",
        summary=None,
        source_metadata={},
    )
    assert result == "short_raw"


def test_short_raw_summary_있으면_None() -> None:
    """짧아도 summary 있으면 cleanup 대상 아님 (메모 등)."""
    result = classify_item(
        source_type="telegram",
        raw_content="짧은 메모",
        summary="유의미한 요약",
        source_metadata={},
    )
    assert result is None


def test_image_priority_extraction_failed보다_낮음() -> None:
    """fetch_error 마킹된 자료가 우연히 image_no_ocr 패턴이면 extraction_failed 가 우선."""
    raw = "[binary file: foo.jpg, mime=image/jpeg]"
    result = classify_item(
        source_type="document",
        raw_content=raw,
        summary=None,
        source_metadata={"fetch_error": "타임아웃"},
    )
    assert result == "extraction_failed"


def test_정상_자료가_긴_raw에_summary_없으면_skip() -> None:
    """raw 는 길지만 summary 없는 케이스 — 백필이 안 끝난 상태로 cleanup 대상 아님."""
    result = classify_item(
        source_type="url",
        raw_content="a" * 1000,
        summary=None,
        source_metadata={},
    )
    # raw 충분 + binary 아님 + fetch_error 없음 → short_raw 분기 안 들어옴 → None
    assert result is None
