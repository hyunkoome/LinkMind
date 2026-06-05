"""writer 의 논문 raw map-reduce 보조 함수 단위 테스트 (pure, CPU).

큰 논문 raw 를 단일 LLM 호출에 다 넣으면 vLLM context(16384)를 초과해 합성이
영구 실패(pending stuck)했다. 이를 막는 섹션 분할/청크 패킹/토큰 추정과 최종
input 예산 가드(_enforce_input_budget)가 의도대로 동작하는지 검증한다.
"""
from __future__ import annotations

from backend.agents import writer as w


# ── _estimate_tokens ──────────────────────────────────────────────────

def test_estimate_tokens_conservative():
    # char/2.5 보수적 추정 — 0 입력은 0, 비례 증가
    assert w._estimate_tokens("") == 0
    assert w._estimate_tokens("a" * 2500) == 1001  # 2500/2.5 + 1


# ── _split_markdown_sections ──────────────────────────────────────────

def test_split_sections_by_headers():
    raw = "# Title\n초록 문단\n## Method\n방법 본문\n### Sub\n세부\n## Results\n결과"
    secs = w._split_markdown_sections(raw)
    # prefix(# Title+초록) + Method + Sub + Results = 4
    assert len(secs) == 4
    assert secs[0].startswith("# Title")
    assert any(s.startswith("## Method") for s in secs)
    assert any(s.startswith("### Sub") for s in secs)


def test_split_sections_empty():
    assert w._split_markdown_sections("") == []
    assert w._split_markdown_sections("   \n  ") == []


def test_split_sections_no_headers_single():
    raw = "헤더 없는 그냥 긴 본문\n여러 줄\n계속"
    secs = w._split_markdown_sections(raw)
    assert len(secs) == 1


# ── _pack_sections_into_chunks ────────────────────────────────────────

def test_pack_respects_budget():
    secs = ["a" * 3000, "b" * 3000, "c" * 3000]
    chunks = w._pack_sections_into_chunks(secs, 6500)
    # 6500 예산 → (a+b) 한 청크, c 한 청크
    assert len(chunks) == 2
    assert all(len(c) <= 6500 for c in chunks)


def test_pack_splits_oversized_single_section():
    # 단일 섹션이 예산 초과 → char 단위 강제 분할 (각 청크 <= 예산)
    secs = ["x" * 20000]
    chunks = w._pack_sections_into_chunks(secs, 6000)
    assert len(chunks) == 4  # ceil(20000/6000)
    assert all(len(c) <= 6000 for c in chunks)
    # 손실 없이 전부 보존
    assert sum(len(c) for c in chunks) == 20000


def test_pack_preserves_order_and_content():
    secs = ["## A\naaa", "## B\nbbb"]
    chunks = w._pack_sections_into_chunks(secs, 100000)
    joined = "\n\n".join(chunks)
    assert "## A" in joined and "## B" in joined
    assert joined.index("## A") < joined.index("## B")


# ── _enforce_input_budget ─────────────────────────────────────────────

def _ctx(excerpt: str) -> dict:
    return {
        "page": {"slug": "test", "title": "T"},
        "primary_raw": {
            "item_id": "00000000-0000-0000-0000-000000000000",
            "title": "T", "source_type": "pdf", "source_url": "u",
            "excerpt": excerpt,
        },
        "sources": [],
        "user_notes_combined": None,
    }


def test_enforce_budget_passthrough_when_small():
    # 작은 prompt 는 그대로 통과 (truncate 안 함)
    template = "<paper_body>\n{paper_body}\n</paper_body>"
    body = "짧은 본문"
    ctx = _ctx(body)
    user_msg = w._build_paper_user_message(template, ctx, [], paper_body_override=body)
    out = w._enforce_input_budget(template, ctx, [], "sys", user_msg, body, 7168)
    assert out == user_msg


def test_enforce_budget_truncates_when_over():
    # 거대한 body 는 예산 안으로 truncate 되어야
    template = "<paper_body>\n{paper_body}\n</paper_body>"
    body = "가" * 200000  # ~80k 토큰 추정 → 예산 초과
    ctx = _ctx(body)
    user_msg = w._build_paper_user_message(template, ctx, [], paper_body_override=body)
    out = w._enforce_input_budget(template, ctx, [], "sys", user_msg, body, 7168)
    assert len(out) < len(user_msg)
    assert "이하 생략" in out
    # 결과가 실제로 예산 안에 들어옴
    budget = w._MODEL_CONTEXT_TOKENS - 7168 - w._PROMPT_SAFETY_MARGIN
    assert w._estimate_tokens("sys") + w._estimate_tokens(out) <= budget + 50


# ── figure 캡션 중복 제거 ─────────────────────────────────────────────

def test_strip_llm_figure_caption_lines():
    body = (
        "## 개요\n본 논문은 X 를 제안한다.\n"
        "그림 1. Waymo 데이터셋 다중 카메라 개요.\n"
        "이어지는 설명."
    )
    out = w._strip_llm_figure_captions(body)
    assert "그림 1. Waymo" not in out          # 캡션형 평문 줄 제거
    assert "본 논문은 X 를 제안한다." in out     # 본문 보존
    assert "이어지는 설명." in out


def test_strip_keeps_inline_figure_references():
    # 문장 중간의 '그림 3에서 보듯' 은 캡션이 아니므로 보존
    body = "그림 3에서 보듯 성능이 향상된다."
    assert w._strip_llm_figure_captions(body) == body


def test_strip_keeps_our_italic_caption():
    # 코드가 삽입하는 *그림 N. ...* 이탤릭 캡션은 '*' 로 시작 → 보존
    body = "*그림 1. 우리가 삽입한 캡션.*"
    assert w._strip_llm_figure_captions(body) == body


def test_insert_inline_figures_strips_placeholders_when_no_figures():
    # figure 가 없으면(빈 리스트) 본문의 [FIGN] placeholder 가 평문으로 남지 않고 제거됨
    body = "## 방법\n설명\n[FIG2]\n더 많은 설명\n[FIG4a-c]\n결과\n[FIG4h, 4i]\n끝"
    out = w._insert_inline_figures(body, [])
    assert "[FIG" not in out                 # 표준/비표준 placeholder 모두 제거
    assert "## 방법" in out and "결과" in out  # 본문은 보존


def test_insert_inline_figures_no_duplicate_caption():
    figures = [{"file_hash": "abc123", "caption": "그림 1. 시스템 개요."}]
    # LLM 이 placeholder + 평문 캡션을 둘 다 쓴 상황
    body = "## 개요\n[FIG1]\n그림 1. 시스템 개요.\n다음 문장."
    out = w._insert_inline_figures(body, figures)
    # 이미지 1개, 이탤릭 캡션 1개만 — 평문 '그림 1. 시스템 개요.' 줄은 사라짐
    assert out.count("![") == 1
    assert out.count("*그림 1. 시스템 개요.*") == 1
    # 평문 캡션 줄(이탤릭 아닌)이 남지 않음
    assert "\n그림 1. 시스템 개요.\n" not in out
