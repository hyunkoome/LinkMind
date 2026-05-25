"""
backend.ingest.url 의 OG meta fallback 단위 테스트 (cpu 마커, 외부 의존 X).

핵심 동작:
- 본문 추출 (trafilatura/readability) 가 성공하면 기존 흐름 그대로.
- 본문 둘 다 실패 + OG meta 있음 → og:title/description/image 로 body 합성.
- og:description 100자 이상이면 abstract 후보로 (옛 200자 cutoff 완화).
- LinkedIn/Facebook 처럼 login wall 페이지 시뮬레이션.
"""

from __future__ import annotations

import pytest

from backend.ingest.url import (
    _og_as_body,
    _parse_og_meta,
    _parse_paper_meta,
    extract_doc,
)


# ─── _parse_og_meta ────────────────────────────────────────────────


def test_parse_og_meta_full():
    """완전한 OG protocol — title/description/image/site_name/type/url."""
    html = """
    <html><head>
        <meta property="og:title" content="OpenAI Releases GPT-X">
        <meta property="og:description" content="A breakthrough model with multimodal capabilities">
        <meta property="og:image" content="https://example.com/preview.png">
        <meta property="og:site_name" content="OpenAI Blog">
        <meta property="og:type" content="article">
        <meta property="og:url" content="https://openai.com/blog/gpt-x">
    </head><body>본문 못 보임</body></html>
    """
    out = _parse_og_meta(html)
    assert out["og:title"] == "OpenAI Releases GPT-X"
    assert out["og:description"].startswith("A breakthrough")
    assert out["og:image"] == "https://example.com/preview.png"
    assert out["og:site_name"] == "OpenAI Blog"
    assert out["og:type"] == "article"
    assert out["og:url"] == "https://openai.com/blog/gpt-x"


def test_parse_og_meta_twitter_card_supplement():
    """OG 없고 Twitter Card 만 있는 페이지 — twitter:* key 도 반환."""
    html = """
    <html><head>
        <meta name="twitter:title" content="Tweet 제목">
        <meta name="twitter:description" content="Twitter card description">
        <meta name="twitter:image" content="https://t.co/img.jpg">
    </head></html>
    """
    out = _parse_og_meta(html)
    assert out["twitter:title"] == "Tweet 제목"
    assert out["twitter:description"] == "Twitter card description"
    assert out["twitter:image"] == "https://t.co/img.jpg"


def test_parse_og_meta_falls_back_to_html_title():
    """OG/Twitter 둘 다 없고 <title> 만 있는 페이지 — og:title 에 매핑."""
    html = "<html><head><title>Plain Old Title</title></head></html>"
    out = _parse_og_meta(html)
    assert out["og:title"] == "Plain Old Title"


def test_parse_og_meta_empty_html_returns_empty_dict():
    """빈 head 페이지 → 빈 dict (오류 안 남)."""
    html = "<html><body>nothing</body></html>"
    out = _parse_og_meta(html)
    assert out == {}


def test_parse_og_meta_strips_whitespace():
    """content 의 앞뒤/내부 공백 정리 (_clean_ws 호출)."""
    html = """
    <html><head>
        <meta property="og:title" content="  Multi    Space   Title  ">
    </head></html>
    """
    out = _parse_og_meta(html)
    assert out["og:title"] == "Multi Space Title"


# ─── _og_as_body ───────────────────────────────────────────────────


def test_og_as_body_empty_dict_returns_empty():
    assert _og_as_body({}) == ""


def test_og_as_body_only_title_meta_returns_empty_if_no_desc():
    """title 만 있고 description 없으면 의미있는 body 만들 수 없으니 빈 string?

    실제 코드: title 있으면 body 생성 (description 없어도 # title 한 줄).
    이게 raw 보존 의도 — 적어도 제목은 LLM 입력으로 가치 있음.
    """
    out = _og_as_body({"og:title": "Just A Title"})
    assert "# Just A Title" in out


def test_og_as_body_full_meta_formats_well():
    """모든 필드 있을 때 body 형식 — title 헤더, 메타, 빈 줄, description."""
    og = {
        "og:title": "Demo Article",
        "og:description": "본문 설명",
        "og:image": "https://e.com/x.jpg",
        "og:site_name": "Demo Site",
        "og:type": "article",
    }
    out = _og_as_body(og)
    assert out.startswith("# Demo Article")
    assert "Site: Demo Site" in out
    assert "Type: article" in out
    assert "Image: https://e.com/x.jpg" in out
    assert out.endswith("본문 설명")  # description 마지막에


def test_og_as_body_prefers_og_over_twitter():
    """og:* 와 twitter:* 둘 다 있으면 og:* 우선."""
    og = {"og:title": "OG Title", "twitter:title": "TW Title", "og:description": "OG desc"}
    out = _og_as_body(og)
    assert "# OG Title" in out
    assert "TW Title" not in out


def test_og_as_body_twitter_fallback():
    """og:* 없고 twitter:* 만 있으면 twitter 사용."""
    og = {"twitter:title": "TW Title", "twitter:description": "TW desc"}
    out = _og_as_body(og)
    assert "# TW Title" in out
    assert "TW desc" in out


# ─── extract_doc 통합 — OG fallback 흐름 ───────────────────────────


def test_extract_doc_normal_body_uses_trafilatura():
    """본문 추출 잘 되는 페이지 — 기존 흐름 그대로 body 채워짐 (OG fallback 안 탐)."""
    html = """
    <html><head><title>Real Article</title>
        <meta property="og:title" content="OG Article">
    </head><body>
        <article><h1>Real Article</h1>
        <p>This is a substantial article body that trafilatura should easily extract.
        It contains enough text to be considered a real article and not stripped out
        as boilerplate. The content discusses meaningful topics in detail.</p>
        <p>More paragraphs to make sure trafilatura keeps this. Lorem ipsum dolor sit
        amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore
        et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation.</p>
        </article>
    </body></html>
    """
    doc = extract_doc(html, url="https://example.com/article")
    assert doc.body
    assert len(doc.body) > 100  # 본문 살아있음
    # title 은 trafilatura 의 추출값이거나 (없으면) og:title fallback
    assert doc.title is not None


def test_extract_doc_login_wall_falls_back_to_og():
    """LinkedIn/Facebook 형 — body 추출 둘 다 실패 + OG meta 있음 → OG body 합성."""
    html = """
    <html><head>
        <meta property="og:title" content="LinkedIn Post by John Doe">
        <meta property="og:description" content="흥미로운 AI 관련 포스트입니다. 사용자 인증 없이도 og 카드는 보임.">
        <meta property="og:image" content="https://media.linkedin.com/preview.png">
        <meta property="og:site_name" content="LinkedIn">
        <meta property="og:type" content="article">
    </head><body>
        <div class="auth-wall">Sign in to LinkedIn</div>
    </body></html>
    """
    doc = extract_doc(html, url="https://linkedin.com/posts/johndoe_xyz")
    assert doc.body, "OG fallback 으로 body 가 채워져야 함"
    assert "LinkedIn Post by John Doe" in doc.body
    assert "흥미로운 AI 관련" in doc.body
    assert "Site: LinkedIn" in doc.body
    assert "https://media.linkedin.com/preview.png" in doc.body
    # title 도 og:title 로 추출
    assert doc.title == "LinkedIn Post by John Doe"


def test_extract_doc_no_body_no_og_returns_empty():
    """본문도 OG meta 도 없으면 빈 ExtractedDoc — 호출자가 placeholder 흐름으로."""
    html = "<html><head></head><body><div class='loading'>Loading...</div></body></html>"
    doc = extract_doc(html, url="https://nodata.example.com/")
    assert doc.body == ""


def test_extract_doc_title_only_og_fallback():
    """본문 없고 og:title 만 있는 경우 — 최소한 title 줄이라도 body 로 (raw 보존 정신)."""
    html = """
    <html><head>
        <meta property="og:title" content="Some Site Title">
    </head><body></body></html>
    """
    doc = extract_doc(html, url="https://example.com/")
    assert doc.body
    assert "# Some Site Title" in doc.body
    assert doc.title == "Some Site Title"


# ─── _parse_paper_meta — abstract 100자 완화 ──────────────────────────


def test_parse_paper_meta_short_og_description_used_after_100_chars():
    """og:description 이 101자 이상이면 abstract 로 채택 (옛 200자 cutoff 완화).

    LinkedIn/Facebook 등의 SNS 카드 description 은 보통 100-200자라 옛 200자
    cutoff 으론 못 잡았음. 100자로 낮춰 SNS 도 abstract 받게.
    """
    # 101자 description
    desc = "A" * 101
    html = f'<html><head><meta property="og:description" content="{desc}"></head></html>'
    abstract, _kw = _parse_paper_meta(html)
    assert abstract == desc


def test_parse_paper_meta_too_short_og_description_skipped():
    """100자 이하면 abstract 로 안 채택 (너무 짧으면 abstract 가치 없음)."""
    desc = "A" * 80
    html = f'<html><head><meta property="og:description" content="{desc}"></head></html>'
    abstract, _kw = _parse_paper_meta(html)
    assert abstract is None


def test_parse_paper_meta_twitter_description_fallback():
    """og:description 없고 twitter:description 있으면 그것도 abstract 후보."""
    desc = "B" * 150
    html = f'<html><head><meta name="twitter:description" content="{desc}"></head></html>'
    abstract, _kw = _parse_paper_meta(html)
    assert abstract == desc
