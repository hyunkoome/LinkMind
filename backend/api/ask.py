"""
POST /ask        — RAG (search → LLM with retrieved context). 단일 응답.
POST /ask/stream — 동일 RAG 흐름 + 답변을 SSE(token) 로 stream.

멀티턴: 클라이언트(localStorage)가 이전 대화 history(user/assistant 턴)를 실어 보낸다.
  - 후속 질문(history 있음)은 LLM 으로 독립형 검색 쿼리로 재작성(condense)한 뒤 검색
    → '그거 더 설명해줘' 같은 대명사/생략 질문도 자료를 제대로 끌어옴.
  - LLM 호출 messages 에 history 를 포함해 맥락(이전 답변 참고) 유지.
백엔드는 stateless — 세션 저장은 멀티테넌트 단계에서 DB 로 이전(§12).
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend import runtime_settings
from backend.api.search import search as _do_search
from backend.db.connection import get_session
from backend.embedding.factory import get_embedding_provider
from backend.embedding.wiki_qdrant import WIKI_STATUS_COMPLETED
from backend.embedding.wiki_qdrant import search_wiki_pages as qdrant_search_wiki_pages
from backend.ingest.arxiv import search_arxiv
from backend.llm.base import ChatMessage, LLMProvider
from backend.llm.factory import get_llm_provider
from backend.schemas.models import (
    AskArxivResult,
    AskCitation,
    AskRelatedWiki,
    AskRequest,
    AskResponse,
    AskTurn,
    SearchRequest,
)

logger = logging.getLogger("linkmind.api.ask")


# 맥락 유지에 포함할 최근 대화 턴 수 (user+assistant 합산). 너무 길면 context 토큰이
# 폭증하고 검색 결과가 자리를 잃으므로 최근 N턴만. 클라이언트도 제한하지만 백엔드에서
# 도 안전하게 자른다.
_MAX_HISTORY_TURNS = 8
# 쿼리 재작성(condense)에 참고할 최근 턴 수 — 직전 맥락만 있으면 충분.
_CONDENSE_HISTORY_TURNS = 6

# 후속 질문 → 독립형 검색 쿼리 재작성용 system prompt. RAG system prompt 와 별개 (이건
# 검색 전처리이지 답변 생성이 아님). 짧고 결정적(temperature=0)으로.
_CONDENSE_SYSTEM = (
    "너는 대화형 검색 시스템의 쿼리 재작성기다. 직전 대화 맥락과 사용자의 후속 질문을 "
    "보고, 그 후속 질문을 맥락 없이도 독립적으로 검색 가능한 한국어 검색 쿼리 한 줄로 "
    "바꿔라. 대명사·생략된 주어를 직전 맥락의 구체적인 명사로 치환한다. 질문이 이미 "
    "독립적이면 거의 그대로 둔다. 설명 없이 검색 쿼리 문자열만 출력한다."
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


# 하이브리드 RAG — 위키 본문 검색. LinkMind 가 자료를 정리해 만든 wiki(linkmind_wiki_pages)
# 를 답변 context 에 함께 넣는다. 위키는 이미 정리된 지식이라 단편 chunk 보다 답변 품질에
# 크게 기여. completed(body 합성 완료) 위키만 대상.
_WIKI_BODY_TOP_K = 3
_WIKI_BODY_CHARS = 1800

_FETCH_WIKI_BODIES_SQL = text("""
    SELECT id, slug, title, description, body
    FROM wiki_pages
    WHERE id = ANY(:ids) AND body IS NOT NULL AND body <> ''
""")


async def _retrieve_wikis(
    search_query: str, session: AsyncSession, *, top_k: int = _WIKI_BODY_TOP_K,
) -> list[dict]:
    """위키 본문 의미검색 — 정리된 지식(위키)을 RAG context 에 포함.

    위키 검색이 실패해도(임베딩/Qdrant 오류) item 기반 RAG 는 그대로 동작하도록 예외를
    삼키고 빈 리스트 반환 (위키는 답변을 풍부하게 하는 보강이지 필수가 아님).
    """
    try:
        embedder = get_embedding_provider()
        emb = await embedder.embed([search_query])
        qv = emb.vectors[0]
        points = await qdrant_search_wiki_pages(
            query_vector=qv, top_k=top_k, status_filter=[WIKI_STATUS_COMPLETED],
        )
        if not points:
            return []
        score_by_id = {str(p.id): float(p.score) for p in points}
        rows = (await session.execute(
            _FETCH_WIKI_BODIES_SQL, {"ids": list(score_by_id.keys())},
        )).mappings().all()
        out = [{
            "slug": r["slug"], "title": r["title"], "description": r["description"],
            "body": r["body"], "score": score_by_id.get(str(r["id"]), 0.0),
        } for r in rows]
        out.sort(key=lambda w: w["score"], reverse=True)
        return out
    except Exception:
        return []


def _wiki_context_block(idx: int, w: dict) -> str:
    """RAG context 의 위키 블록 — 위키 본문(정리된 markdown)을 발췌해 넣는다."""
    body = (w.get("body") or "").strip()[:_WIKI_BODY_CHARS]
    return f"[{idx}] 📖 위키: {w['title']}\nType: wiki (정리된 지식)\n{body}\n"


router = APIRouter()


# SYSTEM_PROMPT 는 DB(prompts 테이블 name='rag_system') 에서 활성 버전을 매 요청마다
# 캐시 hit 로 가져온다. DB 초기 시드 default 는 runtime_settings.RAG_SYSTEM_PROMPT_SEED.
# UI Settings 탭에서 변경하면 새 버전이 저장되고 즉시 다음 요청부터 반영.


async def _condense_query(
    question: str,
    history: list[AskTurn],
    provider: LLMProvider,
    model: str | None,
) -> str:
    """후속 질문을 독립형 검색 쿼리로 재작성. history 비면 원 질문 그대로 반환.

    첫 턴(history 없음)은 LLM 호출을 건너뛰어 불필요한 지연을 막는다. 재작성 실패는
    치명적이지 않으므로(검색 품질만 약간 떨어질 뿐) 예외 시 원 질문으로 fallback.
    """
    if not history:
        return question
    recent = history[-_CONDENSE_HISTORY_TURNS:]
    convo = "\n".join(
        f"{'사용자' if t.role == 'user' else '비서'}: {t.content}" for t in recent
    )
    user_msg = (
        f"[대화 맥락]\n{convo}\n\n[후속 질문]\n{question}\n\n[독립형 검색 쿼리]"
    )
    try:
        resp = await provider.chat(
            messages=[
                ChatMessage(role="system", content=_CONDENSE_SYSTEM),
                ChatMessage(role="user", content=user_msg),
            ],
            model=model,
            temperature=0.0,
            max_tokens=128,
        )
        text_out = (resp.text or "").strip()
        if not text_out:
            return question
        # 한 줄만 — 모델이 군더더기를 붙여도 첫 줄만 검색 쿼리로 사용.
        return text_out.splitlines()[0].strip() or question
    except Exception:
        return question


# ──────────────────────────────────────────────────────────────
# Agentic — arxiv 외부 검색 intent (Phase 4)
# ──────────────────────────────────────────────────────────────
# 로컬 Gemma + 무료 arxiv API 만 사용 (외부 AI 불필요, §14 privacy). 사용자가 "논문
# 찾아줘" 류 의도를 보이면 자료(RAG) 대신 arxiv 를 검색해 외부 논문을 제시한다. 외부
# 검색이라 raw 저장 없음 — "수집" 버튼으로 /ingest/auto 를 탈 때만 §2(raw-first) 흐름.

# 검색할 논문 수 (카드로 보여줄 만큼만 — 너무 많으면 context/UI 가 산만).
_ARXIV_MAX_RESULTS = 6

# intent 판정 + 검색어 추출용 system prompt. 결정적(temperature=0)·JSON 1줄.
# arxiv 는 영어 논문 코퍼스라 검색어는 영어 키워드로 뽑게 한다.
_INTENT_SYSTEM = (
    "너는 대화형 연구 비서의 라우터다. 사용자의 질문이 'arxiv/논문/학술 문헌을 새로 "
    "검색·탐색해 달라'는 의도인지 판정한다. 새 논문을 찾아달라거나 특정 주제의 최신 "
    "연구를 찾는 의도면 intent='arxiv_search', 그 외(이미 가진 자료에 대한 질문·일반 "
    "대화·설명 요청)는 intent='rag'. arxiv_search 면 arxiv 검색에 쓸 영어 키워드 쿼리를 "
    "arxiv_query 에 담는다(논문 검색이 아니면 빈 문자열). 반드시 JSON 한 줄만 출력: "
    '{"intent": "arxiv_search"|"rag", "arxiv_query": "..."}'
)

_INTENT_JSON_RE = re.compile(r"\{.*?\}", re.DOTALL)


async def _detect_intent(
    question: str,
    history: list[AskTurn],
    provider: LLMProvider,
    model: str | None,
) -> tuple[str, str]:
    """질문을 'arxiv_search' 또는 'rag' 로 라우팅 + arxiv 검색어 추출.

    반환: (intent, arxiv_query). 판정/파싱 실패는 치명적이지 않으므로 ('rag', '') 로
    fallback — 기본 RAG 흐름으로 안전하게 진행. 직전 맥락도 참고('더 찾아줘' 류).
    """
    recent = history[-_CONDENSE_HISTORY_TURNS:]
    convo = "\n".join(
        f"{'사용자' if t.role == 'user' else '비서'}: {t.content}" for t in recent
    )
    user_msg = (f"[대화 맥락]\n{convo}\n\n[질문]\n{question}" if convo else question)
    try:
        resp = await provider.chat(
            messages=[
                ChatMessage(role="system", content=_INTENT_SYSTEM),
                ChatMessage(role="user", content=user_msg),
            ],
            model=model,
            temperature=0.0,
            max_tokens=128,
        )
        raw = (resp.text or "").strip()
        m = _INTENT_JSON_RE.search(raw)
        if not m:
            return "rag", ""
        data = json.loads(m.group(0))
        intent = data.get("intent") or "rag"
        if intent != "arxiv_search":
            return "rag", ""
        arxiv_query = (data.get("arxiv_query") or "").strip()
        # 검색어를 못 뽑았으면 외부 검색이 무의미 — 원 질문으로 fallback 검색.
        return "arxiv_search", (arxiv_query or question.strip())
    except Exception:
        return "rag", ""


def _arxiv_context(papers: list[dict]) -> str:
    """arxiv 검색 결과를 답변 생성용 context 블록으로 — 번호[n] + 제목/저자/초록 발췌."""
    if not papers:
        return "[arxiv 검색 결과]\n(검색 결과 없음)"
    blocks = ["[arxiv 검색 결과]"]
    for i, p in enumerate(papers, start=1):
        authors = ", ".join((p.get("authors") or [])[:4])
        if len(p.get("authors") or []) > 4:
            authors += " 외"
        summary = (p.get("summary") or "").strip()[:600]
        blocks.append(
            f"[{i}] {p.get('title') or '(제목 없음)'}\n"
            f"저자: {authors}\n출판: {p.get('published') or ''}\n"
            f"URL: {p.get('abs_url') or ''}\n초록: {summary}"
        )
    return "\n\n".join(blocks)


async def _retrieve_arxiv(
    arxiv_query: str,
) -> tuple[str, list[AskArxivResult], list[dict]]:
    """arxiv 검색 실행 → (context, arxiv_results 스키마 목록, 원본 dict 목록).

    검색이 비어도 graceful — '결과 없음' context 로 답변 생성이 정상 진행된다.
    """
    papers = await search_arxiv(arxiv_query, max_results=_ARXIV_MAX_RESULTS)
    results = [
        AskArxivResult(
            arxiv_id=p["arxiv_id"], title=p["title"], summary=p.get("summary"),
            authors=p.get("authors") or [], published=p.get("published"),
            abs_url=p.get("abs_url") or "", pdf_url=p.get("pdf_url"),
        )
        for p in papers if p.get("title")
    ]
    return _arxiv_context(papers), results, papers


async def _retrieve(
    *,
    search_query: str,
    question_for_pins: AskRequest,
    session: AsyncSession,
) -> tuple[str, list[AskCitation], list[dict]]:
    """pinned items + 위키 본문 + item 벡터검색 결과로 context 와 citations 를 구성.

    하이브리드 RAG: 원본 item chunk 뿐 아니라 정리된 위키 본문도 함께 context 에 넣는다.
    search_query 는 (멀티턴이면) 재작성된 독립형 쿼리. pin_item_ids 는 원 요청에서.
    반환: (context, item citations, 검색된 위키 hits[dict]).
    """
    payload = question_for_pins
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

    # 1) 위키 본문 검색 (하이브리드 RAG) — 정리된 지식을 pinned 다음에 우선 배치.
    #    위키는 자료를 종합·정리한 글이라 단편 chunk 보다 답변에 크게 기여.
    wiki_hits = await _retrieve_wikis(search_query, session)
    for w in wiki_hits:
        context_blocks.append(_wiki_context_block(len(context_blocks) + 1, w))

    # 2) Retrieval — 재작성된 검색 쿼리로 원본 item 벡터검색.
    search_resp = await _do_search(
        SearchRequest(query=search_query, top_k=payload.top_k),
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
    return context, citations, wiki_hits


def _build_messages(
    system_prompt: str,
    history: list[AskTurn],
    context: str,
    question: str,
) -> list[ChatMessage]:
    """LLM 호출 messages 조립 — system + 최근 history + (context+질문) user 턴.

    history 의 assistant 턴은 이전 답변(맥락)으로만 쓰고, context(검색 자료)는 현재
    질문 기준으로만 새로 붙인다 — 매 턴 자료를 다시 끌어오므로 최신 검색이 반영됨.
    """
    msgs: list[ChatMessage] = [ChatMessage(role="system", content=system_prompt)]
    for t in history[-_MAX_HISTORY_TURNS:]:
        msgs.append(ChatMessage(role=t.role, content=t.content))
    msgs.append(ChatMessage(
        role="user", content=f"[Context]\n{context}\n\n[Question]\n{question}",
    ))
    return msgs


async def _fetch_related_wikis(
    citations: list[AskCitation], session: AsyncSession,
) -> list[AskRelatedWiki]:
    """citations 의 item_id 들에서 link 된 wiki_pages 집계 (우측 panel 표시용)."""
    item_ids = [str(c.item_id) for c in citations]
    if not item_ids:
        return []
    rows = (await session.execute(
        _RELATED_WIKIS_SQL, {"item_ids": item_ids, "limit": 10},
    )).mappings().all()
    return [
        AskRelatedWiki(
            slug=r["slug"],
            title=r["title"],
            description=r["description"],
            body_status=r["body_status"],
            overlap=int(r["overlap"]),
        )
        for r in rows
    ]


def _merge_searched_wikis(
    related: list[AskRelatedWiki], wiki_hits: list[dict],
) -> list[AskRelatedWiki]:
    """citations 역추적 위키 + 본문 직접검색된 위키 병합 (slug 중복 제거).
    하이브리드 RAG 로 직접 검색돼 답변에 쓰인 위키도 우측 패널에 노출."""
    existing = {w.slug for w in related}
    for wh in wiki_hits:
        if wh["slug"] in existing:
            continue
        related.append(AskRelatedWiki(
            slug=wh["slug"], title=wh["title"],
            description=wh.get("description"), body_status="completed", overlap=0,
        ))
        existing.add(wh["slug"])
    return related


@router.post("", response_model=AskResponse)
async def ask(
    payload: AskRequest,
    session: AsyncSession = Depends(get_session),
) -> AskResponse:
    provider_name = payload.llm_provider or runtime_settings.get_effective_llm_provider()
    provider = get_llm_provider(provider_name)

    # agentic 라우팅 — arxiv 의도 감지. 단 arxiv 가 RAG 를 *대체*하지 않는다(2026-06-04
    # 회귀 수정): 자료 기반 RAG 는 항상 수행하고, arxiv 의도일 때만 외부 논문을 추가로
    # 병합한다. 그래야 '논문 알려줘' 같은 질문이 arxiv 로 오분류돼도 내 위키/자료가
    # 누락되지 않는다 (RAG 가 핵심, arxiv 는 보강).
    intent, arxiv_query = await _detect_intent(
        payload.question, payload.history, provider, payload.llm_model,
    )

    # 후속 질문이면 독립형 검색 쿼리로 재작성 (첫 턴이면 원 질문 그대로).
    search_query = await _condense_query(
        payload.question, payload.history, provider, payload.llm_model,
    )
    context, citations, wiki_hits = await _retrieve(
        search_query=search_query, question_for_pins=payload, session=session,
    )

    # arxiv 의도면 외부 논문 검색 결과를 context 에 추가 (RAG 자료 다음에 보강).
    arxiv_results: list[AskArxivResult] = []
    if intent == "arxiv_search":
        arxiv_ctx, arxiv_results, _ = await _retrieve_arxiv(arxiv_query)
        context = f"{context}\n\n{arxiv_ctx}"

    _, system_prompt = runtime_settings.get_active_prompt("rag_system")
    messages = _build_messages(system_prompt, payload.history, context, payload.question)
    resp = await provider.chat(messages=messages, model=payload.llm_model)

    related_wikis = _merge_searched_wikis(
        await _fetch_related_wikis(citations, session), wiki_hits,
    )

    return AskResponse(
        question=payload.question,
        answer=resp.text,
        citations=citations,
        related_wikis=related_wikis,
        intent=intent,
        arxiv_results=arxiv_results,
        llm_provider=resp.provider,
        llm_model=resp.model,
    )


def _sse(obj: dict) -> str:
    """SSE data 프레임 한 줄 — UTF-8(한국어) 보존을 위해 ensure_ascii=False."""
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


@router.post("/stream")
async def ask_stream(
    payload: AskRequest,
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """멀티턴 RAG 답변을 SSE 로 stream.

    프레임 순서:
      1) {"type":"meta", citations, related_wikis, llm_provider, llm_model, search_query}
         — 검색이 끝나는 즉시 1회. 프론트가 우측 위키 패널/인용을 답변보다 먼저 표시.
      2) {"type":"token", "text": "..."}  — 답변 델타. 여러 번.
      3) {"type":"done"}  또는  {"type":"error", "message": ...}
    검색/condense 는 stream 시작 전에 동기로 끝내고, LLM 답변 생성만 stream 한다.
    """
    provider_name = payload.llm_provider or runtime_settings.get_effective_llm_provider()
    provider = get_llm_provider(provider_name)

    # agentic 라우팅 — arxiv 는 RAG 를 대체하지 않고 보강한다 (2026-06-04 회귀 수정,
    # ask() 와 동일). RAG 는 항상 수행, arxiv 의도일 때만 외부 논문을 context 에 추가.
    intent, arxiv_query = await _detect_intent(
        payload.question, payload.history, provider, payload.llm_model,
    )
    search_query = await _condense_query(
        payload.question, payload.history, provider, payload.llm_model,
    )
    context, citations, wiki_hits = await _retrieve(
        search_query=search_query, question_for_pins=payload, session=session,
    )
    related_wikis = _merge_searched_wikis(
        await _fetch_related_wikis(citations, session), wiki_hits,
    )

    arxiv_results: list[AskArxivResult] = []
    if intent == "arxiv_search":
        arxiv_ctx, arxiv_results, _ = await _retrieve_arxiv(arxiv_query)
        context = f"{context}\n\n{arxiv_ctx}"

    _, system_prompt = runtime_settings.get_active_prompt("rag_system")
    messages = _build_messages(system_prompt, payload.history, context, payload.question)

    # stream 에선 응답 객체가 없어 정확한 model id 를 모름 — payload 명시값 또는 provider
    # default(_default_model). 표시용이므로 best-effort.
    model_name = payload.llm_model or getattr(provider, "_default_model", "") or ""

    async def event_gen() -> AsyncIterator[str]:
        yield _sse({
            "type": "meta",
            "question": payload.question,
            "search_query": search_query,
            "intent": intent,
            "citations": [c.model_dump(mode="json") for c in citations],
            "related_wikis": [w.model_dump() for w in related_wikis],
            "arxiv_results": [a.model_dump(mode="json") for a in arxiv_results],
            "llm_provider": provider.name,
            "llm_model": model_name,
        })
        try:
            async for delta in provider.stream_chat(
                messages=messages, model=payload.llm_model,
            ):
                yield _sse({"type": "token", "text": delta})
        except Exception as exc:  # noqa: BLE001 — 어떤 LLM 오류든 클라이언트에 전달
            yield _sse({"type": "error", "message": str(exc)})
            return
        yield _sse({"type": "done"})

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # nginx 등 reverse proxy 의 응답 버퍼링 비활성 — 토큰이 즉시 흘러가게.
            "X-Accel-Buffering": "no",
        },
    )
