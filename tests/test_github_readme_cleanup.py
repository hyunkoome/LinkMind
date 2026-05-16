"""
backend.ingest.github._clean_readme_html 단위 테스트.

GitHub README 안의 raw HTML 만 strip 하고 안의 정보 (URL/alt 텍스트) 는 보존.
"""

from __future__ import annotations

from backend.ingest.github import _clean_readme_html


def test_strips_h2_tag_keeps_text():
    md = "## Markdown title\n<h2 style=\"color: red;\">CVPR 2026 Highlight</h2>\nBody."
    out = _clean_readme_html(md)
    assert "## Markdown title" in out
    assert "CVPR 2026 Highlight" in out
    assert "<h2" not in out
    assert 'style="color' not in out


def test_a_tag_becomes_markdown_link():
    md = '<a href="https://arxiv.org/abs/2511.20343">paper</a>'
    out = _clean_readme_html(md)
    assert out == "[paper](https://arxiv.org/abs/2511.20343)"


def test_a_tag_without_text_uses_href():
    md = '<a href="https://example.com"></a>'
    out = _clean_readme_html(md)
    assert "https://example.com" in out


def test_img_tag_preserves_alt():
    md = '<img src="logo.png" alt="Project Logo">'
    out = _clean_readme_html(md)
    assert "Project Logo" in out
    assert "<img" not in out


def test_img_without_alt_is_removed():
    md = "Before <img src=\"badge.svg\"> after"
    out = _clean_readme_html(md)
    assert "<img" not in out
    assert "Before" in out
    assert "after" in out


def test_paper_link_inside_html_is_recovered():
    """OmniVGGT README 와 유사 — <a href="arxiv..."> 안에 paper link.

    중요: _detect_paper_links 가 cleaned 텍스트에서도 잡을 수 있어야 (cross-modal
    topic 자동 매핑의 단서).
    """
    md = (
        '<h1>OmniVGGT</h1>\n'
        '<a href="https://arxiv.org/abs/2511.10560" target="_blank">paper</a>\n'
        '<a href="https://github.com/foo/bar">code</a>'
    )
    out = _clean_readme_html(md)
    # markdown link 로 보존 → URL 정규식 인식
    assert "https://arxiv.org/abs/2511.10560" in out
    assert "https://github.com/foo/bar" in out


def test_code_block_text_preserved_no_html_tags():
    md = "<pre><code>pip install foo</code></pre>"
    out = _clean_readme_html(md)
    assert "pip install foo" in out
    assert "<code>" not in out


def test_empty_input():
    assert _clean_readme_html("") == ""
    assert _clean_readme_html(None) == ""  # type: ignore[arg-type]


def test_markdown_without_html_stays_basically_same():
    md = "## Title\n\n- item 1\n- item 2\n\n[link](https://x.com)"
    out = _clean_readme_html(md)
    # markdown 자체는 손상 X
    assert "## Title" in out
    assert "item 1" in out
    assert "[link](https://x.com)" in out
