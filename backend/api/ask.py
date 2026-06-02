"""
POST /ask — RAG (search → LLM with retrieved context).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend import runtime_settings
from backend.api.search import search as _do_search
from backend.db.connection import get_session
from backend.llm.base import ChatMessage
from backend.llm.factory import get_llm_provider
from backend.schemas.models import (
    AskCitation,
    AskRelatedWiki,
    AskRequest,
    AskResponse,
    SearchRequest,
)


# citation 의 item_id 들에서 link 된 wiki_pages 집계 — overlap 큰 순서.
# 한 wiki 가 여러 citation 의 item 과 link 됐다면 관련도 더 강함.
_RELATED_WIKIS_SQL = text("""
    SELECT wp.slug, wp.title, wp.description, wp.body_status,
           COUNT(*) AS overlap
    FROM wiki_page_items wpi
    JOIN wiki_pages wp ON wp.id = wpi.wiki_page_id
    WHERE wpi.item_id = ANY(:item_ids)
      AND (wpi.user_action IS NULL OR wpi.user_action != 'removed')
    GROUP BY wp.id, wp.slug, wp.title, wp.description, wp.body_status, wp.is_pinned
    ORDER BY overlap DESC, wp.is_pinned DESC
    LIMIT :limit
""")

_FETCH_PINNED_ITEMS_SQL = text("""
    SELECT id, title, source_url, source_type, summary, tags
    FROM items WHERE id = ANY(:ids)
""")


def _context_block(
    idx: int,
    *,
    title: str | None,
    url: str | None,
    source_type: str | None,
    tags: list[str] | None,
    summary: str | None,
    snippet: str | None,
) -> str:
    """RAG context 한 항목 [idx] 블록 — pinned/search hit 공용 포맷.

    우선순위: summary (한국어 bullet 요약) → snippet (chunk 발췌). summary 가 단편
    chunk 보다 정보 밀도 높아 답변 품질의 핵심 (ask.py 원래 주석 참고).
    """
    tag_line = ""
    if tags:
        tag_line = "Tags: " + " ".join(f"#{t}" for t in list(tags)[:10]) + "\n"
    block = f"[{idx}] {title or '(no title)'}\nURL: {url or ''}\nSource: {source_type}\n{tag_line}"
    if summary:
        block += f"요약:\n{summary[:1500]}\n"
    if snippet:
        block += f"관련 chunk:\n{snippet[:400]}\n"
    return block


router = APIRouter()


# SYSTEM_PROMPT 는 DB(prompts 테이블 name='rag_system') 에서 활성 버전을 매 요청마다
# 캐시 hit 로 가져온다. DB 초기 시드 default 는 runtime_settings.RAG_SYSTEM_PROMPT_SEED.
# UI Settings 탭에서 변경하면 새 버전이 저장되고 즉시 다음 요청부터 반영.


@router.post("", response_model=AskResponse)
async def ask(
    payload: AskRequest,
    session: AsyncSession = Depends(get_session),
) -> AskResponse:
    context_blocks: list[str] = []
    citations: list[AskCitation] = []
    pinned_ids: set[str] = set()

    # 0) Pinned items — ask 에서 URL 붙여 방금 ingest 한 자료. 벡터검색 타이밍(임베딩
    #    인덱싱 race)/순위 누락에 안 맡기고 반드시 context 최상단 [1..] 에 포함.
    if payload.pin_item_ids:
        id_strs = [str(i) for i in payload.pin_item_ids]
        prows = (await session.execute(
            _FETCH_PINNED_ITEMS_SQL, {"ids": id_strs},
        )).mappings().all()
        by_id = {str(r["id"]): r for r in prows}
        for pid in id_strs:                          # pin 순서 유지
            r = by_id.get(pid)
            if not r:
                continue
            context_blocks.append(_context_block(
                len(context_blocks) + 1,
                title=r["title"], url=r["source_url"], source_type=r["source_type"],
                tags=list(r["tags"] or []), summary=(r["summary"] or "").strip(),
                snippet=None,
            ))
            citations.append(AskCitation(
                item_id=r["id"], title=r["title"], source_url=r["source_url"], snippet=None,
            ))
            pinned_ids.add(pid)

    # 1) Retrieval
    search_resp = await _do_search(
        SearchRequest(query=payload.question, top_k=payload.top_k),
        session=session,
    )

    # 2) Build context — chunk snippet (vector hit 의 핵심 구간) + item 의 한국어 요약
    #    (사람이 봤을 때 가장 정보 밀도 높음) + 태그 / 출처. 이게 RAG 의 자료 기반
    #    답변 품질의 핵심 — chunk 만 보내면 LLM 이 단편적인 발췌만 보고 답해서 일반
    #    정의 응답이 나오기 쉬움. 자료의 깊이를 LLM 이 활용하게 하는 게 LinkMind 의
    #    가치. (pinned 와 중복되는 item 은 skip — 번호/내용 중복 방지.)
    for hit in search_resp.hits:
        if str(hit.item_id) in pinned_ids:
            continue
        context_blocks.append(_context_block(
            len(context_blocks) + 1,
            title=hit.title, url=hit.source_url, source_type=hit.source_type,
            tags=hit.tags, summary=(hit.summary or "").strip(),
            snippet=(hit.snippet or "").strip(),
        ))
        citations.append(AskCitation(
            item_id=hit.item_id,
            title=hit.title,
            source_url=hit.source_url,
            snippet=hit.snippet,
        ))
    context = "\n\n".join(context_blocks) if context_blocks else "(검색 결과 없음)"

    # 3) LLM — provider 는 명시값(있으면) 또는 effective default(runtime override → env).
    provider_name = payload.llm_provider or runtime_settings.get_effective_llm_provider()
    provider = get_llm_provider(provider_name)
    user_msg = f"[Context]\n{context}\n\n[Question]\n{payload.question}"
    _, system_prompt = runtime_settings.get_active_prompt("rag_system")
    resp = await provider.chat(
        messages=[
            ChatMessage(role="system", content=system_prompt),
            ChatMessage(role="user", content=user_msg),
        ],
        model=payload.llm_model,
    )

    # 4) Related wikis — citations 의 item_id 들에서 link 된 wiki_pages 집계.
    #    /ask 페이지의 우측 panel 에 표시 (사용자가 클릭하면 wiki detail 로 이동).
    related_wikis: list[AskRelatedWiki] = []
    item_ids = [str(c.item_id) for c in citations]
    if item_ids:
        rows = (await session.execute(
            _RELATED_WIKIS_SQL, {"item_ids": item_ids, "limit": 10},
        )).mappings().all()
        related_wikis = [
            AskRelatedWiki(
                slug=r["slug"],
                title=r["title"],
                description=r["description"],
                body_status=r["body_status"],
                overlap=int(r["overlap"]),
            )
            for r in rows
        ]

    return AskResponse(
        question=payload.question,
        answer=resp.text,
        citations=citations,
        related_wikis=related_wikis,
        llm_provider=resp.provider,
        llm_model=resp.model,
    )
