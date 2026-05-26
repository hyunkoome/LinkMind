"""
GET/POST /wiki/* — D10 llm_wiki API.

docs/llm_wiki_design.md 참조. endpoints:

  - GET  /wiki                            — wiki page 리스트 (filter + pagination)
  - GET  /wiki/{slug}                     — wiki page 상세 (body stale 면 eager 재합성)
  - POST /wiki/{slug}/regenerate          — 명시적 writer 재호출
  - POST /wiki/search                     — wiki body 단위 검색 (wave-1f Qdrant 전: FTS fallback)
  - POST /wiki/classify                   — item(s) → wiki_pages 자동 분류

wave-1e (2026-05-26) — minimum viable. Qdrant body embedding 검색은 wave-1f,
ingest BackgroundTask hook 은 wave-2.
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents import AgentContext
from backend.agents.classifier import ClassifierAgent
from backend.agents.retriever import _build_wiki_context
from backend.agents.writer import WriterAgent
from backend.db.connection import get_session
from backend.embedding.factory import get_embedding_provider
from backend.embedding.wiki_qdrant import search_wiki_pages as qdrant_search_wiki_pages
from backend.schemas.models import (
    WikiClassifyItemResult,
    WikiClassifyRequest,
    WikiClassifyResponse,
    WikiCrossLink,
    WikiKeywordSearchResponse,
    WikiKeywordSuggestion,
    WikiKeywordsUpdateRequest,
    WikiKeywordsUpdateResponse,
    WikiPageDetail,
    WikiPageListItem,
    WikiPageListResponse,
    WikiRegenerateResponse,
    WikiSearchHit,
    WikiSearchRequest,
    WikiSearchResponse,
    WikiSource,
)

logger = logging.getLogger("linkmind.api.wiki")

router = APIRouter()


# ────────────────────────────────────────────────────────────────
# GET /wiki — list
# ────────────────────────────────────────────────────────────────

_LIST_PAGES_SQL = text("""
    SELECT
        wp.id, wp.topic_id, wp.slug, wp.title, wp.description,
        wp.body_status, wp.body_generated_at, wp.is_pinned, wp.updated_at,
        (SELECT COUNT(*) FROM wiki_page_items wpi
            WHERE wpi.wiki_page_id = wp.id
              AND (wpi.user_action IS NULL OR wpi.user_action != 'removed')
        ) AS source_count
    FROM wiki_pages wp
    WHERE (CAST(:status AS TEXT) IS NULL OR wp.body_status = CAST(:status AS TEXT))
      AND (CAST(:q AS TEXT) IS NULL OR (
           wp.title ILIKE '%' || CAST(:q AS TEXT) || '%'
           OR wp.description ILIKE '%' || CAST(:q AS TEXT) || '%'
           OR wp.slug ILIKE '%' || CAST(:q AS TEXT) || '%'
      ))
      AND (CAST(:keyword AS TEXT) IS NULL OR CAST(:keyword AS TEXT) = ANY(wp.keywords))
    ORDER BY wp.is_pinned DESC, wp.updated_at DESC
    LIMIT :limit OFFSET :offset
""")


_COUNT_PAGES_SQL = text("""
    SELECT COUNT(*) FROM wiki_pages wp
    WHERE (CAST(:status AS TEXT) IS NULL OR wp.body_status = CAST(:status AS TEXT))
      AND (CAST(:q AS TEXT) IS NULL OR (
           wp.title ILIKE '%' || CAST(:q AS TEXT) || '%'
           OR wp.description ILIKE '%' || CAST(:q AS TEXT) || '%'
           OR wp.slug ILIKE '%' || CAST(:q AS TEXT) || '%'
      ))
      AND (CAST(:keyword AS TEXT) IS NULL OR CAST(:keyword AS TEXT) = ANY(wp.keywords))
""")


@router.get("", response_model=WikiPageListResponse)
async def list_wiki_pages(
    status: str | None = Query(default=None, description="empty/generating/ready/stale"),
    q: str | None = Query(default=None, description="title/description/slug 부분 매칭"),
    keyword: str | None = Query(default=None, description="keywords 배열에서 정확 매칭"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> WikiPageListResponse:
    params = {"status": status, "q": q, "keyword": keyword, "limit": limit, "offset": offset}
    total = (await session.execute(_COUNT_PAGES_SQL, params)).scalar() or 0
    rows = (await session.execute(_LIST_PAGES_SQL, params)).mappings().all()
    pages = [
        WikiPageListItem(
            id=r["id"],
            topic_id=r["topic_id"],
            slug=r["slug"],
            title=r["title"],
            description=r["description"],
            body_status=r["body_status"],
            body_generated_at=r["body_generated_at"],
            source_count=int(r["source_count"] or 0),
            is_pinned=bool(r["is_pinned"]),
            updated_at=r["updated_at"],
        )
        for r in rows
    ]
    return WikiPageListResponse(total=int(total), pages=pages)


# ────────────────────────────────────────────────────────────────
# GET /wiki/{slug} — 상세 (lazy/eager 재합성)
# ────────────────────────────────────────────────────────────────

_FETCH_PAGE_BY_SLUG_SQL = text("SELECT id FROM wiki_pages WHERE slug = :slug")


async def _wiki_context_to_response(wiki_context: dict) -> WikiPageDetail:
    """retriever 의 _build_wiki_context 결과 → API 응답 형태."""
    page = wiki_context["page"]
    sources = [
        WikiSource(
            item_id=UUID(s["item_id"]),
            title=s.get("title"),
            summary=s.get("summary"),
            source_type=s.get("source_type", "?"),
            source_url=s.get("source_url"),
            confidence=s.get("confidence"),
            role=s.get("role"),
            user_action=None,    # 별 join 없이는 missing — 향후 확장 시 추가
            tags=s.get("tags") or [],
            user_notes=s.get("user_notes"),
            is_read=bool(s.get("is_read", False)),
            attachment_count=len(s.get("attachments") or []),
        )
        for s in wiki_context["sources"]
    ]
    cross_links = [
        WikiCrossLink(slug=c["slug"], title=c["title"], shared_items=c["shared_items"])
        for c in wiki_context.get("cross_link_candidates", [])
    ]
    return WikiPageDetail(
        id=UUID(page["id"]),
        topic_id=UUID(page["topic_id"]) if page.get("topic_id") else None,
        slug=page["slug"],
        title=page["title"],
        description=page.get("description"),
        variant=page.get("variant", "default"),
        body=page.get("body"),
        body_status=page.get("body_status", "empty"),
        body_model=None,
        body_prompt_version=None,
        body_generated_at=None,
        latest_version=int(page.get("latest_version") or 0),
        is_pinned=bool(page.get("is_pinned", False)),
        user_overrides=page.get("user_overrides"),
        keywords=list(page.get("keywords") or []),
        sources=sources,
        cross_links=cross_links,
        user_notes_combined=wiki_context.get("user_notes_combined"),
    )


@router.get("/{slug}", response_model=WikiPageDetail)
async def get_wiki_page(
    slug: str,
    regenerate: bool = Query(default=False, description="강제 재합성 (status 무시)"),
    session: AsyncSession = Depends(get_session),
) -> WikiPageDetail:
    """wiki page 조회. body 가 'empty' 또는 'stale' 면 자동 eager 합성 (writer 호출).

    regenerate=true 면 body_status 무관 무조건 재합성.
    """
    page_id = (await session.execute(_FETCH_PAGE_BY_SLUG_SQL, {"slug": slug})).scalar()
    if not page_id:
        raise HTTPException(status_code=404, detail=f"wiki page not found: slug={slug}")

    # 현재 상태 확인 (body_status 만 가볍게)
    status_row = (await session.execute(
        text("SELECT body_status FROM wiki_pages WHERE id = :pid"), {"pid": str(page_id)},
    )).first()
    current_status = status_row[0] if status_row else "empty"

    # 합성 필요한지 판단
    needs_gen = regenerate or current_status in ("empty", "stale")

    if needs_gen and current_status != "generating":
        # writer agent 호출 (eager — 사용자가 기다림)
        # 사용자 명시 (2026-05-26): "그때그때 위키화" — UX 일관
        ctx = AgentContext(
            session=session,
            related_wiki_page_id=page_id,
            extra={
                "trigger_reason": "user_request" if regenerate else (
                    "stale_regenerate" if current_status == "stale" else "first_gen"
                ),
            },
        )
        writer = WriterAgent()
        wr_result = await writer.run(ctx)
        await session.commit()
        if not wr_result.ok:
            logger.warning(
                "writer 실패 (slug=%s): %s", slug, wr_result.error,
            )
            # 실패해도 일단 현재 wiki_context 반환 (옛 body 가 있으면 그거)

    # 최종 wiki_context build + response (read-only — commit 불필요)
    wiki_context = await _build_wiki_context(session, page_id)
    return await _wiki_context_to_response(wiki_context)


# ────────────────────────────────────────────────────────────────
# POST /wiki/{slug}/regenerate — 명시적 writer 재호출
# ────────────────────────────────────────────────────────────────

@router.post("/{slug}/regenerate", response_model=WikiRegenerateResponse)
async def regenerate_wiki_page(
    slug: str,
    session: AsyncSession = Depends(get_session),
) -> WikiRegenerateResponse:
    page_id = (await session.execute(_FETCH_PAGE_BY_SLUG_SQL, {"slug": slug})).scalar()
    if not page_id:
        raise HTTPException(status_code=404, detail=f"wiki page not found: slug={slug}")

    ctx = AgentContext(
        session=session,
        related_wiki_page_id=page_id,
        extra={"trigger_reason": "user_request"},
    )
    writer = WriterAgent()
    wr_result = await writer.run(ctx)
    await session.commit()

    meta = wr_result.output_meta or {}
    return WikiRegenerateResponse(
        page_id=page_id,
        slug=slug,
        ok=wr_result.ok,
        body_length=meta.get("body_length"),
        version_number=meta.get("version_number"),
        duration_ms=wr_result.duration_ms,
        error=wr_result.error,
    )


# ────────────────────────────────────────────────────────────────
# POST /wiki/search — wiki body 단위 검색 (wave-1f Qdrant 전: FTS fallback)
# ────────────────────────────────────────────────────────────────

_FTS_SEARCH_SQL = text("""
    WITH ranked AS (
        SELECT
            wp.id, wp.slug, wp.title, wp.description, wp.body, wp.body_status,
            (SELECT COUNT(*) FROM wiki_page_items wpi
                WHERE wpi.wiki_page_id = wp.id
                  AND (wpi.user_action IS NULL OR wpi.user_action != 'removed')
            ) AS source_count,
            -- 단순 BM25-ish ranking: title 매칭 가중치 높음, body / description 중간
            (
                CASE WHEN wp.title ILIKE '%' || :q || '%' THEN 0.5 ELSE 0 END
              + CASE WHEN wp.description ILIKE '%' || :q || '%' THEN 0.3 ELSE 0 END
              + CASE WHEN wp.body ILIKE '%' || :q || '%' THEN 0.2 ELSE 0 END
            ) AS score,
            CASE
                WHEN wp.body ILIKE '%' || :q || '%' THEN 'body'
                WHEN wp.description ILIKE '%' || :q || '%' THEN 'description'
                WHEN wp.title ILIKE '%' || :q || '%' THEN 'title'
                ELSE 'unknown'
            END AS matched_in
        FROM wiki_pages wp
        WHERE wp.title ILIKE '%' || :q || '%'
           OR wp.description ILIKE '%' || :q || '%'
           OR wp.body ILIKE '%' || :q || '%'
    )
    SELECT * FROM ranked
    WHERE score > 0
    ORDER BY score DESC, source_count DESC
    LIMIT :top_k
""")


_FETCH_PAGES_BY_IDS_SQL = text("""
    SELECT
        wp.id, wp.slug, wp.title, wp.description, wp.body, wp.body_status,
        (SELECT COUNT(*) FROM wiki_page_items wpi
            WHERE wpi.wiki_page_id = wp.id
              AND (wpi.user_action IS NULL OR wpi.user_action != 'removed')
        ) AS source_count
    FROM wiki_pages wp WHERE wp.id = ANY(:ids)
""")


@router.post("/search", response_model=WikiSearchResponse)
async def search_wiki_pages(
    payload: WikiSearchRequest,
    session: AsyncSession = Depends(get_session),
) -> WikiSearchResponse:
    """wiki body 단위 검색 (wave-1f, §0.3 검색 fix 의 핵심).

    1) Qdrant wiki_pages 컬렉션 (body embedding) top-K — 의미 단위 검색
    2) 결과 빈 경우 (body 아직 없거나 Qdrant 빈 경우) → FTS fallback (ILIKE)
    """
    q = (payload.query or "").strip()
    if not q:
        return WikiSearchResponse(query=payload.query, hits=[])

    # 1) Qdrant 의미 검색 우선
    try:
        embedder = get_embedding_provider()
        emb = await embedder.embed([q])
        points = await qdrant_search_wiki_pages(
            query_vector=emb.vectors[0], top_k=payload.top_k,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Qdrant wiki search 실패, FTS fallback: %s", exc)
        points = []

    if points:
        page_ids = [str(p.id) for p in points]
        rows = (await session.execute(
            _FETCH_PAGES_BY_IDS_SQL, {"ids": page_ids},
        )).mappings().all()
        db_by_id = {str(r["id"]): r for r in rows}
        hits: list[WikiSearchHit] = []
        for p in points:
            r = db_by_id.get(str(p.id))
            if not r:
                continue
            body = r["body"] or ""
            excerpt = body[:300] if body else r["description"]
            hits.append(WikiSearchHit(
                page_id=r["id"],
                slug=r["slug"],
                title=r["title"],
                description=r["description"],
                score=float(p.score),
                body_excerpt=excerpt,
                source_count=int(r["source_count"] or 0),
                body_status=r["body_status"],
                matched_in="body",
            ))
        if hits:
            return WikiSearchResponse(query=payload.query, hits=hits)

    # 2) FTS fallback — Qdrant 비어있거나 매칭 없을 때
    fts_rows = (await session.execute(
        _FTS_SEARCH_SQL, {"q": q, "top_k": payload.top_k},
    )).mappings().all()
    fts_hits: list[WikiSearchHit] = []
    for r in fts_rows:
        body = (r["body"] or "")
        excerpt = body[:300] if body else r["description"]
        fts_hits.append(WikiSearchHit(
            page_id=r["id"],
            slug=r["slug"],
            title=r["title"],
            description=r["description"],
            score=float(r["score"]),
            body_excerpt=excerpt,
            source_count=int(r["source_count"] or 0),
            body_status=r["body_status"],
            matched_in=r["matched_in"],
        ))
    return WikiSearchResponse(query=payload.query, hits=fts_hits)


# ────────────────────────────────────────────────────────────────
# POST /wiki/classify — items → wiki_pages 자동 분류
# ────────────────────────────────────────────────────────────────

@router.post("/classify", response_model=WikiClassifyResponse)
async def classify_items(
    payload: WikiClassifyRequest,
    session: AsyncSession = Depends(get_session),
) -> WikiClassifyResponse:
    """item(s) 를 wiki_pages 에 자동 분류. 단일 item_id 또는 batch item_ids 가능.

    wave-1e: 동기 처리 (작은 batch). 큰 batch (16,463 items 전체) 는 wave-2 의
    BackgroundTask + 진행률 polling.
    """
    item_ids: list[UUID]
    if payload.item_id:
        item_ids = [payload.item_id]
    elif payload.item_ids:
        item_ids = list(payload.item_ids)
    else:
        raise HTTPException(status_code=400, detail="item_id 또는 item_ids 필수")

    results: list[WikiClassifyItemResult] = []
    succeeded = 0
    failed = 0

    classifier = ClassifierAgent()
    for iid in item_ids:
        ctx = AgentContext(
            session=session,
            related_item_id=iid,
            extra={"threshold": payload.threshold},
        )
        try:
            ag_result = await classifier.run(ctx)
            await session.commit()
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            logger.exception("classifier 호출 실패 (item=%s)", iid)
            results.append(WikiClassifyItemResult(
                item_id=iid, ok=False, error=str(exc),
            ))
            failed += 1
            continue

        meta = ag_result.output_meta or {}
        # linked_page_ids 에서 slug 추출 (별 query 1 회) — 작은 batch 라 OK
        linked_slugs: list[str] = []
        if meta.get("linked_page_ids"):
            slug_rows = (await session.execute(
                text("SELECT slug FROM wiki_pages WHERE id = ANY(:ids)"),
                {"ids": meta["linked_page_ids"]},
            )).all()
            linked_slugs = [r[0] for r in slug_rows]

        results.append(WikiClassifyItemResult(
            item_id=iid,
            ok=ag_result.ok,
            matched_count=int(meta.get("matched_count", 0)),
            new_pages_count=int(meta.get("new_pages_count", 0)),
            linked_page_slugs=linked_slugs,
            duration_ms=ag_result.duration_ms,
            error=ag_result.error,
        ))
        if ag_result.ok:
            succeeded += 1
        else:
            failed += 1

    return WikiClassifyResponse(
        processed=len(item_ids),
        succeeded=succeeded,
        failed=failed,
        results=results,
    )


# ────────────────────────────────────────────────────────────────
# Keywords — wave-2d (사용자 추가/삭제 + autocomplete)
# ────────────────────────────────────────────────────────────────

_FETCH_KEYWORDS_SQL = text("""
    SELECT keywords FROM wiki_pages WHERE slug = :slug
""")


_UPDATE_KEYWORDS_API_SQL = text("""
    UPDATE wiki_pages SET keywords = :keywords WHERE slug = :slug
    RETURNING keywords
""")


# unnest 로 모든 wiki_pages 의 keywords 전체 → DISTINCT + ILIKE + 빈도 정렬
# (대규모 — 23k pages × N keywords, GIN index 가 ILIKE 가속).
_SEARCH_KEYWORDS_SQL = text("""
    SELECT keyword, COUNT(*) AS usage_count
    FROM wiki_pages, UNNEST(keywords) AS keyword
    WHERE CAST(:q AS TEXT) IS NULL OR keyword ILIKE '%' || CAST(:q AS TEXT) || '%'
    GROUP BY keyword
    ORDER BY usage_count DESC, keyword ASC
    LIMIT :limit
""")


@router.post("/{slug}/keywords", response_model=WikiKeywordsUpdateResponse)
async def update_wiki_keywords(
    slug: str,
    payload: WikiKeywordsUpdateRequest,
    session: AsyncSession = Depends(get_session),
) -> WikiKeywordsUpdateResponse:
    """사용자가 wiki page 의 keywords 추가/삭제. dedup (case-insensitive).

    추가:
      - 입력 keyword 가 DB 에 이미 있으면 그대로 (autocomplete suggestion)
      - 없으면 신규 등록 (이 wiki page 의 keywords 에 추가)
    삭제:
      - 명시된 keyword 만 keywords array 에서 제거 (case-insensitive 매칭)
    """
    row = (await session.execute(_FETCH_KEYWORDS_SQL, {"slug": slug})).first()
    if not row:
        raise HTTPException(status_code=404, detail=f"wiki page not found: slug={slug}")

    current: list[str] = list(row[0] or [])

    # case-insensitive dedup helper
    def _normalize(kw: str) -> str:
        return kw.strip()

    remove_set = {kw.lower() for kw in payload.remove if kw}
    after_remove = [kw for kw in current if kw.lower() not in remove_set]
    actually_removed = [kw for kw in current if kw.lower() in remove_set]

    current_lower = {kw.lower() for kw in after_remove}
    actually_added: list[str] = []
    final = list(after_remove)
    for raw in payload.add:
        kw = _normalize(raw)
        if not kw or len(kw) > 80:
            continue
        if kw.lower() in current_lower:
            continue
        final.append(kw)
        current_lower.add(kw.lower())
        actually_added.append(kw)

    updated_row = (await session.execute(_UPDATE_KEYWORDS_API_SQL, {
        "slug": slug,
        "keywords": final,
    })).first()
    await session.commit()

    return WikiKeywordsUpdateResponse(
        slug=slug,
        keywords=list((updated_row[0] if updated_row else final)),
        added=actually_added,
        removed=actually_removed,
    )


# /wiki/keywords 의 GET — autocomplete 용. /wiki/{slug} 와 conflict 안 하도록
# /wiki/_keywords 로 prefix 두면 안전. 하지만 명확하게 별 prefix /keywords-search.
@router.get("/_keywords/search", response_model=WikiKeywordSearchResponse)
async def search_keywords(
    q: str | None = Query(default=None, description="prefix/substring 매칭"),
    limit: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> WikiKeywordSearchResponse:
    """전체 wiki_pages 의 keywords UNNEST + DISTINCT — autocomplete.

    q 비면 가장 많이 쓰인 keyword top-N (전체 빈도).
    q 있으면 ILIKE '%q%' 매칭만.
    """
    rows = (await session.execute(_SEARCH_KEYWORDS_SQL, {
        "q": q.strip() if q else None,
        "limit": limit,
    })).mappings().all()
    return WikiKeywordSearchResponse(
        query=q or "",
        suggestions=[
            WikiKeywordSuggestion(keyword=r["keyword"], usage_count=int(r["usage_count"]))
            for r in rows
        ],
    )
