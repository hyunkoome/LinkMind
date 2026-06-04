"""
위키 본문 figure 삽입 검증 (2026-06-04 사용자 요구: 모델/아키텍처 + 결과 그림 필수).

- retriever._figure_priority: 아키텍처(0) → 결과(1) → 기타(2) 우선순위
- writer._append_figures: 본문 끝 '## 그림' 섹션에 markdown 이미지 + caption

순수 함수 — DB/네트워크 없음 (cpu 카테고리).
"""

from __future__ import annotations

from backend.agents.retriever import _figure_priority
from backend.agents.writer import _append_figures


def test_figure_priority_architecture_first():
    assert _figure_priority("Figure 2. Overview of the TerraSeg architecture") == 0
    assert _figure_priority("Fig. 1: System pipeline diagram") == 0
    assert _figure_priority("Figure 3. Network structure") == 0


def test_figure_priority_results_second():
    assert _figure_priority("Figure 4. Qualitative results on nuScenes") == 1
    assert _figure_priority("Fig. 6: Ablation study performance") == 1


def test_figure_priority_other_last():
    assert _figure_priority("Figure 9. dataset samples") == 2
    assert _figure_priority("") == 2


def test_append_figures_basic():
    body = "# 제목\n\n본문 내용"
    out = _append_figures(body, [
        {"file_hash": "abc123", "caption": "Figure 2. Overview of architecture"},
    ])
    assert "## 그림" in out
    assert "![Figure 2. Overview of architecture](/files/abc123)" in out
    assert "*Figure 2. Overview of architecture*" in out
    assert out.startswith("# 제목")           # 본문 보존


def test_append_figures_empty_noop():
    body = "# 제목\n\n본문"
    assert _append_figures(body, []) == body


def test_append_figures_skips_missing_hash():
    body = "본문"
    out = _append_figures(body, [{"file_hash": None, "caption": "x"}])
    # file_hash 없으면 그 figure skip — 결국 추가된 figure 0개면 body 그대로.
    assert out == body


def test_append_figures_url_format():
    # URL 은 코드가 직접 구성 → /files/{hash} 정확 (LLM hallucination 아님).
    out = _append_figures("b", [{"file_hash": "deadbeef", "caption": "c"}])
    assert "/files/deadbeef" in out
