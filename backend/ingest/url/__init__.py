"""
URL ingester — 주어진 URL 의 본문을 추출해서 LinkMind 에 넣는다.

trafilatura 가 1차 추출기. 실패 시 readability-lxml fallback.
HTTP 요청은 httpx async.

사용 예 (REPL):
    >>> import asyncio
    >>> from backend.ingest.url import ingest_url
    >>> asyncio.run(ingest_url("https://arxiv.org/abs/2401.01234"))
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from uuid import UUID

import httpx

from backend.config import get_settings
from backend.db.connection import get_engine
from backend.db.repository import (
    find_item_by_hash,
    insert_chunks,
    insert_item,
    update_item_analysis,
)
from backend.embedding.factory import get_embedding_provider
from backend.embedding.qdrant_store import ensure_collection, upsert_chunks
from backend.llm.base import ChatMessage
from backend.llm.factory import get_llm_provider
from backend.utils.chunking import chunk_text
from backend.utils.hashing import sha256_text
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

logger = logging.getLogger(__name__)

# 요약 생성 시 LLM 에 보낼 raw 본문의 최대 길이. qwen2.5:7b 컨텍스트는 32K 지만
# 안전 마진 + 빠른 응답 위해 앞부분만. 페이지 본문 앞쪽이 보통 가장 중요 (abstract).
_SUMMARY_INPUT_LIMIT = 8000
_SUMMARY_PROMPT_VERSION = "v1"
_SUMMARY_SYSTEM_PROMPT = (
    "You are a concise summarizer for technical research content. "
    "Summarize the input in 3-5 bullet points in Korean. "
    "Preserve technical terms (English) when appropriate."
)


async def fetch_html(url: str, timeout: float = 30.0) -> str:
    """주어진 URL 의 HTML 을 가져온다. 30xx 따라가고 비-2xx 는 raise."""
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=timeout,
        headers={"User-Agent": "LinkMind/0.1 (+https://github.com/Real2Real-AI/LinkMind)"},
    ) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.text


def extract_main_text(html: str, url: str | None = None) -> tuple[str | None, str | None]:
    """본문 텍스트와 제목 추출. (text, title) 반환.

    trafilatura → readability → 빈 결과 순서로 fallback.
    """
    try:
        import trafilatura
        downloaded = trafilatura.extract(
            html,
            url=url,
            include_comments=False,
            include_tables=True,
            favor_recall=True,
        )
        if downloaded:
            md = trafilatura.metadata.extract_metadata(html)
            title = md.title if md and md.title else None
            return downloaded, title
    except Exception as e:                          # noqa: BLE001
        logger.warning("trafilatura 추출 실패: %s", e)

    # Fallback: readability-lxml
    try:
        from readability import Document
        doc = Document(html)
        return doc.summary(html_partial=True), doc.title()
    except Exception as e:                          # noqa: BLE001
        logger.warning("readability fallback 실패: %s", e)

    return None, None


async def ingest_url(url: str, *, analyze_now: bool = True) -> dict[str, Any]:
    """URL 하나를 fetch + extract + DB 저장 + (옵션) 임베딩.

    Returns: {"item_id": str, "created": bool, "chunks_indexed": int}
    """
    html = await fetch_html(url)
    text_body, title = extract_main_text(html, url=url)
    if not text_body or len(text_body.strip()) < 50:
        raise ValueError(f"URL 에서 본문을 추출하지 못했습니다 (너무 짧거나 비었음): {url}")

    content_hash = sha256_text(text_body)

    engine = get_engine()
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        existing = await find_item_by_hash(
            session, source_type="url", content_hash=content_hash
        )
        if existing is not None:
            return {"item_id": str(existing), "created": False, "chunks_indexed": 0}

        item_id = await insert_item(
            session,
            source_type="url",
            raw_content=text_body,
            raw_content_hash=content_hash,
            source_id=None,
            source_url=url,
            source_metadata={},
            title=title,
            source_created_at=None,
        )
        await session.commit()

        chunks_indexed = 0
        summary_text: str | None = None
        if analyze_now:
            chunks_indexed = await _embed_and_index(session, item_id=item_id, text=text_body)
            # 요약은 옵션 — LLM 가 다운/미설정이어도 raw + embedding 은 이미 저장됨.
            summary_text = await _generate_and_save_summary(
                session, item_id=item_id, text=text_body,
            )

        return {
            "item_id": str(item_id),
            "created": True,
            "chunks_indexed": chunks_indexed,
            "summary_generated": summary_text is not None,
            "title": title,
        }


async def _generate_and_save_summary(
    session: AsyncSession, *, item_id: UUID, text: str,
) -> str | None:
    """LLM 으로 한국어 요약 생성 → items.summary 에 저장.

    실패해도 ingest 자체는 계속 — raw_content / embedding 은 영향 없음.
    LLM 미설정/다운 시 None 반환하고 warning 만 로깅.
    """
    try:
        llm = get_llm_provider()
        resp = await llm.chat([
            ChatMessage(role="system", content=_SUMMARY_SYSTEM_PROMPT),
            ChatMessage(role="user", content=text[:_SUMMARY_INPUT_LIMIT]),
        ])
        # summary_model 은 "provider/model" 로 합쳐서 한 컬럼에 — 재학습/재요약 시 어느
        # 시점 어떤 모델로 생성했는지 추적 가능.
        await update_item_analysis(
            session,
            item_id=item_id,
            summary=resp.text,
            summary_model=f"{resp.provider}/{resp.model}",
            summary_prompt_version=_SUMMARY_PROMPT_VERSION,
            categories=None,
            tags=None,
        )
        await session.commit()
        logger.info(
            "요약 생성 완료: item=%s, model=%s/%s, len=%d",
            item_id, resp.provider, resp.model, len(resp.text),
        )
        return resp.text
    except Exception as e:  # noqa: BLE001
        logger.warning("요약 생성 실패 (ingest 는 계속): %s", e)
        return None


async def _embed_and_index(
    session: AsyncSession, *, item_id: UUID, text: str,
) -> int:
    embedder = get_embedding_provider()
    await ensure_collection(dim=embedder.dim)
    chunks = chunk_text(text)
    if not chunks:
        return 0
    emb = await embedder.embed(chunks)
    chunk_ids = await insert_chunks(
        session,
        item_id=item_id,
        chunks=chunks,
        embedding_model=embedder.model,
        embedding_dim=embedder.dim,
    )
    await session.commit()
    payloads = [
        {
            "item_id": str(item_id),
            "chunk_index": idx,
            "source_type": "url",
            "snippet": ctext[:300],
        }
        for idx, ctext in enumerate(chunks)
    ]
    await upsert_chunks(
        chunk_ids=[str(cid) for cid in chunk_ids],
        vectors=emb.vectors,
        payloads=payloads,
    )
    return len(chunks)
# CLI 진입점은 backend/ingest/url/__main__.py 에 분리 (패키지를 `-m` 으로 실행 시
# Python 이 __main__.py 를 찾는 표준 동작).
