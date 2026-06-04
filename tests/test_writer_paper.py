"""
writer 재설계 — 논문(arxiv/pdf) 전용 본문 합성 (2026-06-04).

검증 대상 (순수 함수 — DB/네트워크 없음, cpu 카테고리):
  - retriever._is_paper_source       : 문서 타입 판별 (element A)
  - retriever._extract_markdown_tables : raw markdown 표 블록 추출 (element E)
  - retriever._build_raw_excerpt     : head 발췌 + 뒤쪽 표 보존 (element B/E)
  - writer._parse_numbered_captions  : figure 한글 캡션 파싱 (element C/D)
  - writer._build_paper_user_message : 논문 prompt 채우기 (element B/F)
"""

from __future__ import annotations

from backend.agents.base import load_prompt
from backend.agents.retriever import (
    RAW_HEAD_CHARS,
    _build_raw_excerpt,
    _extract_markdown_tables,
    _is_paper_source,
    _self_wiki_identity_item_id,
)
from backend.agents.writer import (
    _build_paper_user_message,
    _build_user_message,
    _figures_block,
    _insert_inline_figures,
    _parse_numbered_captions,
)
from backend.agents.base import load_prompt as _load_prompt


# ── element A: 문서 타입 판별 ──

def test_is_paper_source():
    assert _is_paper_source({"source_type": "arxiv"})
    assert _is_paper_source({"source_type": "pdf"})
    assert not _is_paper_source({"source_type": "url"})
    assert not _is_paper_source({"source_type": "youtube"})
    assert not _is_paper_source({})


def test_self_wiki_identity_item_id():
    # self-wiki slug → 정체성 item uuid 추출
    assert _self_wiki_identity_item_id(
        "url__item__349afd42-4c38-43af-ae50-d19a2a456cb9"
    ) == "349afd42-4c38-43af-ae50-d19a2a456cb9"
    # 개념(kebab) wiki / 외부ID wiki → None (정체성 단일 item 없음)
    assert _self_wiki_identity_item_id("cosmos-3-omnimodal-world-models") is None
    assert _self_wiki_identity_item_id("github__nvidia-cosmos") is None
    assert _self_wiki_identity_item_id("") is None


# ── element E: markdown 표 추출 ──

def test_extract_markdown_tables_basic():
    md = "intro\n| a | b |\n|---|---|\n| 1 | 2 |\ntext after"
    tables = _extract_markdown_tables(md)
    assert len(tables) == 1
    assert "| a | b |" in tables[0]
    assert "| 1 | 2 |" in tables[0]


def test_extract_markdown_tables_multiple():
    md = "| a |\n| 1 |\nbreak\n| c |\n| 3 |"
    assert len(_extract_markdown_tables(md)) == 2


def test_extract_markdown_tables_ignores_single_line():
    # 한 줄짜리 '|' 는 표가 아님 (헤더+구분선 최소 2줄)
    assert _extract_markdown_tables("| not a table |\nnormal text") == []


# ── element B: raw 발췌 (+ 뒤쪽 표 보존) ──

def test_build_raw_excerpt_short_passthrough():
    raw = "# Title\nshort body"
    assert _build_raw_excerpt(raw) == raw


def test_build_raw_excerpt_empty():
    assert _build_raw_excerpt("") == ""


def test_build_raw_excerpt_appends_tail_tables():
    # head 를 넘는 긴 본문 + 뒤쪽에 표 → head 발췌 뒤 표가 보존돼야 (element E)
    head = "A" * (RAW_HEAD_CHARS + 100)
    table = "\n| metric | score |\n|---|---|\n| IoU | 0.87 |"
    raw = head + table
    out = _build_raw_excerpt(raw)
    assert out.startswith("A" * 100)        # head 발췌됨
    assert "| IoU | 0.87 |" in out          # 뒤쪽 표 보존됨 (잘리지 않음)


def test_build_raw_excerpt_truncates_head():
    raw = "B" * (RAW_HEAD_CHARS + 5000)     # 표 없는 긴 본문
    out = _build_raw_excerpt(raw)
    assert "이하 생략" in out
    assert len(out) < len(raw)


# ── element C/D: figure 한글 캡션 파싱 ──

def test_parse_numbered_captions_ok():
    out = _parse_numbered_captions("1. 그림 1. 아키텍처 개요\n2. 그림 2. 결과 비교", 2)
    assert out == ["그림 1. 아키텍처 개요", "그림 2. 결과 비교"]


def test_parse_numbered_captions_strips_paren_numbering():
    assert _parse_numbered_captions("1) 첫째\n2) 둘째", 2) == ["첫째", "둘째"]


def test_parse_numbered_captions_mismatch_returns_none():
    # 줄 수가 기대와 안 맞으면 None → caller 가 원본 절단 폴백
    assert _parse_numbered_captions("1. only one", 3) is None
    assert _parse_numbered_captions("", 2) is None


# ── element B/F: 논문 user message 빌더 ──

def _paper_ctx():
    return {
        "page": {"slug": "arxiv__2603_27344", "title": "arxiv:2603.27344"},
        "sources": [
            {"item_id": "a", "title": "TerraSeg", "source_type": "arxiv",
             "summary": "self-supervised ground seg", "source_url": "http://arxiv.org/abs/x"},
            {"item_id": "b", "title": "관련 블로그", "source_type": "url", "summary": "blog"},
        ],
        "primary_raw": {
            "item_id": "a",
            "title": "TerraSeg: Self-Supervised Ground Segmentation for Any LiDAR",
            "source_type": "arxiv",
            "source_url": "http://arxiv.org/abs/x",
            "excerpt": "# TerraSeg\nWe propose {x_i} with latex $y$",
        },
        "user_notes_combined": "",
    }


_FIGS_KR = [
    {"file_hash": "h1", "caption": "그림 1. 제안 아키텍처 개요"},
    {"file_hash": "h2", "caption": "그림 4. 정성 결과 비교"},
]


def test_build_paper_user_message_uses_paper_title_and_body():
    tmpl = load_prompt("writer_paper", "v1")["user_template"]
    msg = _build_paper_user_message(tmpl, _paper_ctx(), _FIGS_KR)
    # element F: 논문 원제가 들어감
    assert "TerraSeg: Self-Supervised Ground Segmentation for Any LiDAR" in msg
    # element B: raw 본문 발췌가 들어감 (summary 아님)
    assert "We propose" in msg
    # format() 이 치환값의 중괄호를 재해석하지 않아 깨지지 않음
    assert "{x_i}" in msg
    # element G: figures 블록에 [FIG1] 라벨 + 한글 캡션
    assert "[FIG1]" in msg and "제안 아키텍처 개요" in msg


def test_build_paper_user_message_lists_other_sources_not_primary():
    tmpl = load_prompt("writer_paper", "v1")["user_template"]
    msg = _build_paper_user_message(tmpl, _paper_ctx(), [])
    # primary(arxiv) 는 <paper_body> 로 들어가고, related 에는 보조 source 만
    assert "관련 블로그" in msg


# ── de-clone: self-wiki 는 정체성 item 만 깊이 인용 (cross-link 논문은 listing) ──

def test_general_self_wiki_deep_cites_only_identity():
    tmpl = _load_prompt("writer", "v1")["user_template"]
    ctx = {
        "page": {"slug": "url__item__aaaa", "title": "블로그", "description": "d"},
        "sources": [
            {"item_id": "paper1", "title": "Cosmos Paper", "source_type": "pdf",
             "summary": "PAPER_DEEP_SUMMARY", "confidence": 0.95, "role": "context"},
            {"item_id": "aaaa", "title": "자율주행 Cosmos 블로그", "source_type": "url",
             "summary": "BLOG_OWN_SUMMARY", "confidence": 1.0, "role": "primary"},
        ],
        "identity_item": {"item_id": "aaaa", "title": "자율주행 Cosmos 블로그", "source_type": "url"},
        "user_notes_combined": "",
        "cross_link_candidates": [],
    }
    msg = _build_user_message(tmpl, ctx)
    # 정체성(블로그)만 deep — 블로그 요약은 본문 인용부에, 논문 요약은 listing 뒤로
    assert "BLOG_OWN_SUMMARY" in msg
    listing_pos = msg.find("listing only")
    assert listing_pos != -1
    # 논문 깊이 요약이 deep 영역(listing 이전)에 없어야 (cross-link 클론 방지)
    assert "PAPER_DEEP_SUMMARY" not in msg[:listing_pos]


def test_general_concept_wiki_keeps_top_n_deep():
    # identity_item 없으면(개념 wiki) 기존대로 top-N deep
    tmpl = _load_prompt("writer", "v1")["user_template"]
    ctx = {
        "page": {"slug": "some-concept", "title": "개념", "description": "d"},
        "sources": [
            {"item_id": "x", "title": "A", "source_type": "url", "summary": "SUMM_A",
             "confidence": 1.0, "role": "primary"},
        ],
        "identity_item": None,
        "user_notes_combined": "",
        "cross_link_candidates": [],
    }
    msg = _build_user_message(tmpl, ctx)
    assert "SUMM_A" in msg


# ── element G: figures 블록 + inline 치환 ──

def test_figures_block_labels():
    out = _figures_block(_FIGS_KR)
    assert "[FIG1] 그림 1. 제안 아키텍처 개요" in out
    assert "[FIG2] 그림 4. 정성 결과 비교" in out
    # file_hash 는 프롬프트에 노출 안 함 (코드가 보관)
    assert "h1" not in out


def test_figures_block_empty():
    assert "그림 라벨을 쓰지" in _figures_block([])


def test_insert_inline_figures_replaces_placeholder_in_context():
    body = "## 방법\n\n아키텍처는 다음과 같다.\n\n[FIG1]\n\n## 실험\n\n결과는 우수하다.\n\n[FIG2]\n"
    out = _insert_inline_figures(body, _FIGS_KR)
    # [FIGN] 라벨이 실제 이미지로 치환되고 그 위치(맥락)에 남음
    assert "[FIG1]" not in out and "[FIG2]" not in out
    # alt 는 짧은 라벨("그림 1"), 전체 캡션은 italic 줄로만
    assert "![그림 1](/files/h1)" in out
    assert "*그림 1. 제안 아키텍처 개요*" in out
    assert "![그림 4](/files/h2)" in out
    assert "*그림 4. 정성 결과 비교*" in out
    # 맥락 보존: FIG1 이미지가 '실험' 섹션보다 앞(방법 섹션)에 위치
    assert out.index("/files/h1") < out.index("## 실험")


def test_insert_inline_figures_dedupes_repeated_label():
    # LLM 이 [FIG1] 을 두 번 써도 이미지는 1번만 (중복 삽입 방지)
    body = "## 방법\n\n[FIG1]\n\n다시 [FIG1] 언급\n"
    out = _insert_inline_figures(body, _FIGS_KR[:1])
    assert out.count("/files/h1") == 1


def test_insert_inline_figures_appends_unused_at_end():
    # LLM 이 [FIG2] 를 안 쓴 경우 → 끝 ## 그림 에 보충 (손실 방지)
    body = "## 방법\n\n[FIG1]\n\n본문"
    out = _insert_inline_figures(body, _FIGS_KR)
    assert "/files/h1" in out          # 본문 맥락
    assert "## 그림" in out             # 안 쓰인 FIG2 보충
    assert "/files/h2" in out


def test_insert_inline_figures_no_figures_noop():
    body = "## 방법\n\n본문"
    assert _insert_inline_figures(body, []) == body
