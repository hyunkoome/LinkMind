"""
POST /ask — RAG (search → LLM with retrieved context).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend import runtime_settings
from backend.api.search import search as _do_search
from backend.db.connection import get_session
from backend.llm.base import ChatMessage
from backend.llm.factory import get_llm_provider
from backend.schemas.models import (
    AskCitation,
    AskRequest,
    AskResponse,
    SearchRequest,
)

router = APIRouter()


# SYSTEM_PROMPT 는 DB(prompts 테이블 name='rag_system') 에서 활성 버전을 매 요청마다
# 캐시 hit 로 가져온다. DB 초기 시드 default 는 runtime_settings.RAG_SYSTEM_PROMPT_SEED.
# UI Settings 탭에서 변경하면 새 버전이 저장되고 즉시 다음 요청부터 반영.


@router.post("", response_model=AskResponse)
async def ask(
    payload: AskRequest,
    session: AsyncSession = Depends(get_session),
) -> AskResponse:
    # 1) Retrieval
    search_resp = await _do_search(
        SearchRequest(query=payload.question, top_k=payload.top_k),
        session=session,
    )

    # 2) Build context — chunk snippet (vector hit 의 핵심 구간) + item 의 한국어 요약
    #    (사람이 봤을 때 가장 정보 밀도 높음) + 태그 / 출처. 이게 RAG 의 자료 기반
    #    답변 품질의 핵심 — chunk 만 보내면 LLM 이 단편적인 발췌만 보고 답해서 일반
    #    정의 응답이 나오기 쉬움. 자료의 깊이를 LLM 이 활용하게 하는 게 LinkMind 의
    #    가치.
    context_blocks: list[str] = []
    citations: list[AskCitation] = []
    for i, hit in enumerate(search_resp.hits, start=1):
        title = hit.title or "(no title)"
        url = hit.source_url or ""
        # 우선순위: summary (한국어 bullet 요약, 보통 500-1500자) → snippet (chunk 발췌)
        summary = (hit.summary or "").strip()
        snippet = (hit.snippet or "").strip()
        # tags 도 같이 — 자료 분야 / 모달리티 단서
        tag_line = ""
        if hit.tags:
            tag_line = "Tags: " + " ".join(f"#{t}" for t in hit.tags[:10]) + "\n"
        block = f"[{i}] {title}\nURL: {url}\nSource: {hit.source_type}\n{tag_line}"
        if summary:
            # summary 가 너무 길면 cap — 1500자 (한국어 5-8 bullet 분량)
            block += f"요약:\n{summary[:1500]}\n"
        if snippet:
            block += f"관련 chunk:\n{snippet[:400]}\n"
        context_blocks.append(block)
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

    return AskResponse(
        question=payload.question,
        answer=resp.text,
        citations=citations,
        llm_provider=resp.provider,
        llm_model=resp.model,
    )
