"""ask 의 RAG context 블록 포맷 단위 테스트.

URL-paste-ingest (2026-06-02) 로 pinned item 과 search hit 가 같은 포맷·연속 번호로
context 에 들어가야 한다. _context_block 은 그 한 항목을 만드는 순수 함수.
"""

from __future__ import annotations

from backend.api.ask import _context_block


def test_block_basic_numbering_and_fields():
    b = _context_block(
        1, title="LeRobot", url="https://x.com/lerobot", source_type="github",
        tags=None, summary="요약 본문", snippet=None,
    )
    assert b.startswith("[1] LeRobot")
    assert "URL: https://x.com/lerobot" in b
    assert "Source: github" in b
    assert "요약:\n요약 본문" in b
    # snippet 없으면 chunk 줄 없음
    assert "관련 chunk" not in b


def test_block_tags_line():
    b = _context_block(
        3, title="t", url=None, source_type="url",
        tags=["slam", "lidar"], summary=None, snippet=None,
    )
    assert b.startswith("[3] t")
    assert "Tags: #slam #lidar" in b


def test_block_summary_capped_at_1500():
    long_summary = "가" * 3000
    b = _context_block(
        2, title="t", url=None, source_type="url",
        tags=None, summary=long_summary, snippet=None,
    )
    # 요약 라벨 길이를 빼고 본문이 1500자로 잘렸는지
    body = b.split("요약:\n", 1)[1]
    assert len(body.strip()) == 1500


def test_block_snippet_capped_at_400():
    b = _context_block(
        1, title="t", url=None, source_type="url",
        tags=None, summary=None, snippet="x" * 1000,
    )
    body = b.split("관련 chunk:\n", 1)[1]
    assert len(body.strip()) == 400


def test_block_missing_title():
    b = _context_block(
        5, title=None, url=None, source_type="url",
        tags=None, summary=None, snippet=None,
    )
    assert b.startswith("[5] (no title)")
