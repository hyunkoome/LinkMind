"""
POST /ask — RAG (search → LLM with retrieved context).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.search import search as _do_search
from backend.config import get_settings
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


SYSTEM_PROMPT = """당신은 사용자의 개인 기술 연구 지식베이스(LinkMind)를 검색해 답변하는 비서입니다.

규칙:
- 제공된 [Context] 항목들만 근거로 답변합니다.
- 답변 끝에 어떤 항목을 근거로 했는지 [n] 형태로 인용 번호를 표기합니다.
- Context에 없으면 추측하지 말고 "관련 자료가 충분하지 않습니다"라고 답합니다.
- 기술 용어(SLAM, 3DGS, LiDAR 등)는 원문 그대로 유지합니다.
- 답변은 한국어로, 간결하게.
"""


@router.post("", response_model=AskResponse)
async def ask(
    payload: AskRequest,
    session: AsyncSession = Depends(get_session),
) -> AskResponse:
    settings = get_settings()

    # 1) Retrieval
    search_resp = await _do_search(
        SearchRequest(query=payload.question, top_k=payload.top_k),
        session=session,
    )

    # 2) Build context
    context_blocks: list[str] = []
    citations: list[AskCitation] = []
    for i, hit in enumerate(search_resp.hits, start=1):
        title = hit.title or "(no title)"
        url = hit.source_url or ""
        snippet = hit.snippet or hit.summary or ""
        context_blocks.append(f"[{i}] {title}\n{url}\n{snippet}")
        citations.append(AskCitation(
            item_id=hit.item_id,
            title=hit.title,
            source_url=hit.source_url,
            snippet=hit.snippet,
        ))
    context = "\n\n".join(context_blocks) if context_blocks else "(검색 결과 없음)"

    # 3) LLM
    provider_name = payload.llm_provider or settings.default_llm_provider
    provider = get_llm_provider(provider_name)
    user_msg = f"[Context]\n{context}\n\n[Question]\n{payload.question}"
    resp = await provider.chat(
        messages=[
            ChatMessage(role="system", content=SYSTEM_PROMPT),
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
