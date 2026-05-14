"""
텍스트 chunking 유틸.

bge-m3는 8K context를 지원하지만, retrieval 품질을 위해 더 작게 자르는 게 일반적.
초기 MVP는 단순 문자 기준 슬라이딩 윈도우. Phase 2에서 토큰 기반/semantic chunking으로 교체 가능.
"""

from __future__ import annotations


def chunk_text(
    text: str,
    target_chars: int = 1200,
    overlap_chars: int = 150,
) -> list[str]:
    """문단 경계를 우선 존중하면서 target_chars 부근에서 자른다.

    1) 빈 줄(\\n\\n)로 1차 분할
    2) 누적해서 target_chars를 넘으면 chunk 확정
    3) chunk 간 overlap_chars 만큼 끝부분 중첩 (RAG recall 보조)
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= target_chars:
        return [text]

    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    buf: list[str] = []
    buf_len = 0

    for p in paragraphs:
        if buf_len + len(p) + 2 > target_chars and buf:
            chunks.append("\n\n".join(buf))
            # overlap: 마지막 chunk 끝부분을 다음 chunk 앞에 다시 둠
            tail = chunks[-1][-overlap_chars:] if overlap_chars > 0 else ""
            buf = [tail] if tail else []
            buf_len = len(tail)
        buf.append(p)
        buf_len += len(p) + 2

    if buf:
        chunks.append("\n\n".join(buf))

    # 한 문단이 너무 길어 target_chars를 단독 초과하는 경우 강제 분할
    final: list[str] = []
    for c in chunks:
        if len(c) <= target_chars * 1.5:
            final.append(c)
            continue
        # 강제 슬라이딩
        i = 0
        while i < len(c):
            final.append(c[i:i + target_chars])
            i += target_chars - overlap_chars
    return final
