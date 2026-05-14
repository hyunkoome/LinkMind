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
)
from backend.embedding.factory import get_embedding_provider
from backend.embedding.qdrant_store import ensure_collection, upsert_chunks
from backend.utils.chunking import chunk_text
from backend.utils.hashing import sha256_text
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

logger = logging.getLogger(__name__)


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
        if analyze_now:
            chunks_indexed = await _embed_and_index(session, item_id=item_id, text=text_body)

        return {
            "item_id": str(item_id),
            "created": True,
            "chunks_indexed": chunks_indexed,
            "title": title,
        }


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


# ----------------------------------------------------------------------------
# CLI 진입점 — `python -m backend.ingest.url <url> [<url> ...]`
# ----------------------------------------------------------------------------
if __name__ == "__main__":
    import asyncio
    import sys

    if len(sys.argv) < 2:
        print("usage: python -m backend.ingest.url <url> [<url> ...]")
        raise SystemExit(2)

    async def _run() -> None:
        for u in sys.argv[1:]:
            try:
                result = await ingest_url(u)
                print(f"OK  {u}  →  {result}")
            except Exception as e:                  # noqa: BLE001
                print(f"ERR {u}  →  {e}")

    asyncio.run(_run())
