"""
backend.ingest.pdf._extract_pdf_title 단위 테스트.

PDF metadata 의 Title 이 자동 생성된 placeholder ('Microsoft Word - paper.docx' 등)
인 경우 body 첫 부분에서 paper title 후보를 뽑는 흐름.
"""

from __future__ import annotations

from backend.ingest.pdf import _extract_pdf_title, _is_placeholder_title


# ── placeholder 판별 ────────────────────────────────────────


def test_placeholder_short():
    assert _is_placeholder_title("")
    assert _is_placeholder_title("ab")


def test_placeholder_microsoft_word():
    assert _is_placeholder_title("Microsoft Word - paper-final.docx")
    assert _is_placeholder_title("microsoft word")


def test_placeholder_untitled():
    assert _is_placeholder_title("Untitled")
    assert _is_placeholder_title("untitled document 1")


def test_placeholder_doc_suffix():
    assert _is_placeholder_title("paper-final.docx")
    assert _is_placeholder_title("manuscript.tex")


def test_not_placeholder_real_title():
    assert not _is_placeholder_title("FAST-LIVO2: Fast, Direct LiDAR-Inertial-Visual Odometry")
    assert not _is_placeholder_title("Attention Is All You Need")


# ── _extract_pdf_title ──────────────────────────────────────


def test_metadata_title_used_when_real():
    info = {"Title": "Attention Is All You Need"}
    body = "first body line\n"
    assert _extract_pdf_title(info, body) == "Attention Is All You Need"


def test_placeholder_metadata_falls_back_to_body():
    info = {"Title": "Microsoft Word - paper-final.docx"}
    body = "\nFAST-LIVO2: Fast, Direct LiDAR-Inertial-Visual\nOdometry\nChunran Zheng..."
    out = _extract_pdf_title(info, body)
    assert out is not None
    assert "FAST-LIVO2" in out


def test_body_first_line_when_no_metadata():
    info = {}
    body = "Redesigning SLAM for Arbitrary Multi-Camera Systems\nJuichung Kuo, Manasi Muglikar"
    assert _extract_pdf_title(info, body) == "Redesigning SLAM for Arbitrary Multi-Camera Systems"


def test_skips_publication_header():
    """'This paper has been accepted at ICRA ...' 같은 헤더는 skip — SLAM Multi-Cam 케이스."""
    info = {}
    body = (
        "This paper has been accepted for publication at the\n"
        "IEEE International Conference on Robotics and Automation (ICRA), Paris, 2020.\n"
        "Redesigning SLAM for Arbitrary Multi-Camera Systems\n"
        "Authors: ..."
    )
    out = _extract_pdf_title(info, body)
    assert out is not None
    assert "SLAM" in out or "Redesigning" in out
    assert "ICRA" not in out  # 헤더는 skip 됐어야


def test_skips_page_numbers_and_short_lines():
    info = {}
    body = "1\n\n2026\n\nA Real Paper Title Here\nAuthor info"
    out = _extract_pdf_title(info, body)
    # 숫자만 / 년도만 인 줄들 skip, 실제 title 잡힘
    assert out == "A Real Paper Title Here"


def test_stops_at_abstract_marker():
    info = {}
    body = "Cool Paper Title\n\nAbstract—This paper proposes..."
    out = _extract_pdf_title(info, body)
    assert out == "Cool Paper Title"


def test_returns_none_when_body_useless():
    """짧은/숫자만/마커뿐인 body 면 None — 호출자가 fallback 처리."""
    info = {}
    body = "1\n\n2\n\nAbstract—body\n"
    assert _extract_pdf_title(info, body) is None
