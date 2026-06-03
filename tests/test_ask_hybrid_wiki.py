"""
하이브리드 RAG — 위키 본문 통합 헬퍼 단위 테스트 (cpu, pure 함수).

ask 가 답변할 때 원본 item chunk + 위키 본문을 함께 쓰는데, 그중 순수 함수
(_merge_searched_wikis, _wiki_context_block)를 검증. 임베딩/Qdrant 검색(_retrieve_wikis)
은 embedding 마커 영역이라 여기선 제외. 2026-06-03 단계 3.
"""

from __future__ import annotations

from backend.api.ask import _merge_searched_wikis, _wiki_context_block
from backend.schemas.models import AskRelatedWiki


def _rw(slug: str, overlap: int = 1) -> AskRelatedWiki:
    return AskRelatedWiki(
        slug=slug, title=slug.upper(), description=None,
        body_status="completed", overlap=overlap,
    )


def test_merge_adds_new_searched_wiki():
    related = [_rw("a")]
    hits = [{"slug": "b", "title": "B", "description": "d"}]
    out = _merge_searched_wikis(related, hits)
    assert [w.slug for w in out] == ["a", "b"]


def test_merge_dedups_by_slug():
    # 이미 citations 역추적으로 있는 slug 는 본문검색에서 중복 추가 안 됨.
    related = [_rw("a", overlap=5)]
    hits = [{"slug": "a", "title": "A-dup", "description": "x"}]
    out = _merge_searched_wikis(related, hits)
    assert len(out) == 1
    assert out[0].overlap == 5  # 원래 것 유지 (덮어쓰지 않음)


def test_merge_empty_hits_noop():
    related = [_rw("a")]
    assert _merge_searched_wikis(related, []) == related


def test_wiki_context_block_format_and_cap():
    block = _wiki_context_block(3, {"title": "Factor Graph", "body": "내용 " * 2000})
    assert block.startswith("[3] 📖 위키: Factor Graph")
    assert "정리된 지식" in block
    # 본문은 1800자로 캡 — 블록 전체가 그 근처(헤더 포함 2000 미만)
    assert len(block) < 2000


def test_wiki_context_block_handles_missing_body():
    block = _wiki_context_block(1, {"title": "T", "body": None})
    assert "[1] 📖 위키: T" in block
