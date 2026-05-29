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

import asyncio
import logging
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents import AgentContext
from backend.agents.classifier import ClassifierAgent
from backend.agents.retriever import _build_wiki_context
from backend.agents.writer import WriterAgent
from backend.db.connection import get_session
from backend.embedding.factory import get_embedding_provider
from backend.embedding.qdrant_store import delete_chunks_for_item
from backend.embedding.wiki_qdrant import (
    delete_wiki_page as qdrant_delete_wiki_page,
)
from backend.embedding.wiki_qdrant import (
    ensure_wiki_collection,
    upsert_wiki_page,
)
from backend.embedding.wiki_qdrant import search_wiki_pages as qdrant_search_wiki_pages
from backend.schemas.models import (
    WikiBatchRegenerateRequest,
    WikiBatchRegenerateResponse,
    WikiClassifyItemResult,
    WikiClassifyRequest,
    WikiClassifyResponse,
    WikiCrossLink,
    WikiKeywordSearchResponse,
    WikiKeywordSuggestion,
    WikiKeywordsUpdateRequest,
    WikiKeywordsUpdateResponse,
    WikiPageDeleteResponse,
    WikiPageDetail,
    WikiPageEditRequest,
    WikiPageListItem,
    WikiPageListResponse,
    WikiRegenerateResponse,
    WikiSearchHit,
    WikiSearchRequest,
    WikiSearchResponse,
    WikiSource,
    WikiStatsResponse,
)

logger = logging.getLogger("linkmind.api.wiki")

router = APIRouter()


# ────────────────────────────────────────────────────────────────
# GET /wiki — list
# ────────────────────────────────────────────────────────────────

# wiki list 검색 범위 (2026-05-27 확장):
#   1) wp.title / description / slug — wiki 자체 메타
#   2) wp.body — LLM 합성 본문 (인용된 source 명/URL 포함될 수 있음)
#   3) sources (linked items) 의 title / source_url / source_metadata.alt_urls / user_notes
#      - 사용자 사례 1 (unite.ai): source URL 'unite.ai' 인데 wiki title 다름
#      - 사용자 사례 2 (hada.io/29686): dedup 으로 옛 item 재사용 → 새 URL 은
#        user_notes 의 caption 으로 보존됨. 그것도 매칭해야 검색됨.
#      - alt_urls (source_metadata jsonb 배열): 미래 dedup 신규 URL 누적 위치
#   4) keywords — 사용자가 명시한 태그
# 23k wiki 라 seq scan OK (작은 MVP 데이터). GIN trgm 은 Phase 3+.
_SEARCH_PREDICATE = """
    wp.title ILIKE '%' || CAST(:q AS TEXT) || '%'
    OR wp.description ILIKE '%' || CAST(:q AS TEXT) || '%'
    OR wp.slug ILIKE '%' || CAST(:q AS TEXT) || '%'
    OR wp.body ILIKE '%' || CAST(:q AS TEXT) || '%'
    OR EXISTS (
        SELECT 1 FROM wiki_page_items wpi
        JOIN items i ON i.id = wpi.item_id
        WHERE wpi.wiki_page_id = wp.id
          AND (wpi.user_action IS NULL OR wpi.user_action != 'removed')
          AND (
              i.title ILIKE '%' || CAST(:q AS TEXT) || '%'
              OR i.source_url ILIKE '%' || CAST(:q AS TEXT) || '%'
              OR i.user_notes ILIKE '%' || CAST(:q AS TEXT) || '%'
              OR (i.source_metadata::jsonb -> 'alt_urls')::text ILIKE
                  '%' || CAST(:q AS TEXT) || '%'
          )
    )
    OR EXISTS (
        SELECT 1 FROM UNNEST(wp.keywords) AS kw
        WHERE kw ILIKE '%' || CAST(:q AS TEXT) || '%'
    )
"""

# 2026-05-27: 통일 후 status alias 제거. backend = frontend = 3 종 (ready /
# pending / completed). 단순 매칭만.
_STATUS_PREDICATE = """
    CAST(:status AS TEXT) IS NULL
    OR wp.body_status = CAST(:status AS TEXT)
"""

# 다중 키워드 필터 (2026-05-29) — AND 의미: 선택한 키워드를 **모두** 가진 wiki 만.
# @> (array contains). 빈/NULL keywords 컬럼은 COALESCE 로 '{}' 취급. :keywords 가
# NULL (선택 없음) 이면 필터 없이 모두 통과 (CAST 분기 short-circuit).
_KEYWORDS_PREDICATE = """
    CAST(:keywords AS text[]) IS NULL
    OR COALESCE(wp.keywords, ARRAY[]::text[]) @> CAST(:keywords AS text[])
"""

_LIST_SELECT = f"""
    SELECT
        wp.id, wp.topic_id, wp.slug, wp.title, wp.description,
        wp.body_status, wp.body_generated_at,
        wp.body_processing_started_at, wp.keywords,
        wp.is_pinned, wp.created_at, wp.updated_at,
        (SELECT COUNT(*) FROM wiki_page_items wpi
            WHERE wpi.wiki_page_id = wp.id
              AND (wpi.user_action IS NULL OR wpi.user_action != 'removed')
        ) AS source_count
    FROM wiki_pages wp
    WHERE ({_STATUS_PREDICATE})
      AND (CAST(:q AS TEXT) IS NULL OR ({_SEARCH_PREDICATE}))
      AND ({_KEYWORDS_PREDICATE})
"""

# 그룹/pinned 우선 정렬 — 전체 tab 의 status 그룹 (completed→pending→issues) +
# pending tab 의 generating 우선 + pinned. 사용자가 고른 sort 는 이 뒤에 붙음
# (그룹 안에서 정렬). 단일 tab 일 때 그룹 ordinal 은 모두 0 이라 무의미.
_GROUP_ORDER = """
    CASE
        WHEN CAST(:status AS TEXT) IS NULL THEN
            CASE wp.body_status
                WHEN 'completed' THEN 0 WHEN 'pending' THEN 1
                WHEN 'issues' THEN 2 ELSE 3 END
        ELSE 0
    END ASC,
    (CAST(:status AS TEXT) = 'pending'
     AND wp.body_processing_started_at IS NOT NULL) DESC,
    wp.is_pinned DESC
"""

# 사용자 선택 정렬 — allowlist (SQL injection 방지: key 로만 lookup, 값은 고정).
# 날짜는 합성 시각(body_generated_at) 우선, 없으면 updated_at. 동률 tie-break 로
# title 추가해 페이지 간 안정적 순서 (pagination 일관).
_SORT_CLAUSES: dict[str, str] = {
    "recent": "COALESCE(wp.body_generated_at, wp.updated_at) DESC NULLS LAST, LOWER(wp.title) ASC",
    "oldest": "COALESCE(wp.body_generated_at, wp.updated_at) ASC NULLS LAST, LOWER(wp.title) ASC",
    "alpha": "LOWER(wp.title) ASC, wp.created_at ASC",
    "alpha_desc": "LOWER(wp.title) DESC, wp.created_at ASC",
}
DEFAULT_WIKI_SORT = "recent"


def _build_list_sql(sort: str):
    """sort key (allowlist) 로 ORDER BY 를 조립한 list SQL 반환."""
    clause = _SORT_CLAUSES.get(sort, _SORT_CLAUSES[DEFAULT_WIKI_SORT])
    return text(
        f"{_LIST_SELECT}\n    ORDER BY {_GROUP_ORDER},\n        {clause}\n"
        "    LIMIT :limit OFFSET :offset"
    )


_COUNT_PAGES_SQL = text(f"""
    SELECT COUNT(*) FROM wiki_pages wp
    WHERE ({_STATUS_PREDICATE})
      AND (CAST(:q AS TEXT) IS NULL OR ({_SEARCH_PREDICATE}))
      AND ({_KEYWORDS_PREDICATE})
""")


@router.get("", response_model=WikiPageListResponse)
async def list_wiki_pages(
    status: str | None = Query(default=None, description="issues/pending/completed"),
    q: str | None = Query(default=None, description="title/description/slug 부분 매칭"),
    keyword: list[str] = Query(
        default=[],
        description="다중 키워드 (AND) — ?keyword=A&keyword=B. 모두 가진 wiki 만",
    ),
    sort: str = Query(
        default=DEFAULT_WIKI_SORT,
        description="recent(최신)/oldest(오래된)/alpha(가나다)/alpha_desc(역순)",
    ),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> WikiPageListResponse:
    # 빈 list → None (필터 없음). non-empty → AND array-contains.
    params = {
        "status": status, "q": q,
        "keywords": keyword or None,
        "limit": limit, "offset": offset,
    }
    total = (await session.execute(_COUNT_PAGES_SQL, params)).scalar() or 0
    rows = (await session.execute(_build_list_sql(sort), params)).mappings().all()
    pages = [
        WikiPageListItem(
            id=r["id"],
            topic_id=r["topic_id"],
            slug=r["slug"],
            title=r["title"],
            description=r["description"],
            body_status=r["body_status"],
            body_generated_at=r["body_generated_at"],
            body_processing_started_at=r["body_processing_started_at"],
            source_count=int(r["source_count"] or 0),
            is_pinned=bool(r["is_pinned"]),
            keywords=list(r["keywords"] or []),
            created_at=r["created_at"],
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
        body_status=page.get("body_status", "ready"),
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
        created_at=page.get("created_at"),
        updated_at=page.get("updated_at"),
    )


@router.get("/{slug}", response_model=WikiPageDetail)
async def get_wiki_page(
    slug: str,
    regenerate: bool = Query(default=False, description="명시 재합성 (body 덮어쓰기)"),
    session: AsyncSession = Depends(get_session),
) -> WikiPageDetail:
    """wiki page 조회. 2026-05-27 사용자 명시: lazy 합성 제거.
    - 'pending' / 'issues' 자료는 daemon 또는 batch 가 처리. 사용자 GET 으로
      LLM 호출 X (중복 처리 방지). frontend 가 'completed' 만 클릭 가능.
    - 명시적 재합성은 regenerate=true (또는 detail page 의 [재합성] 버튼).
    """
    page_id = (await session.execute(_FETCH_PAGE_BY_SLUG_SQL, {"slug": slug})).scalar()
    if not page_id:
        raise HTTPException(status_code=404, detail=f"wiki page not found: slug={slug}")

    if regenerate:
        ctx = AgentContext(
            session=session,
            related_wiki_page_id=page_id,
            extra={"trigger_reason": "user_request"},
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
# PATCH /wiki/{slug} — title / description / body 수동 편집 (2026-05-27)
# ────────────────────────────────────────────────────────────────
#
# 사용자가 LLM 합성 결과를 수동 보강 / 수정. body 가 변경되면 body_status='completed',
# body_model='user', 새 wiki_page_versions row 적립 (학습 신호 보존), Qdrant body
# embedding 재upsert.

_FETCH_PAGE_FULL_SQL = text("""
    SELECT id, slug, title, description, body, body_status,
           is_pinned, keywords
    FROM wiki_pages WHERE slug = :slug
""")

_LATEST_VERSION_NUMBER_SQL = text("""
    SELECT COALESCE(MAX(version_number), 0) FROM wiki_page_versions WHERE page_id = :page_id
""")

_UPDATE_PAGE_FIELDS_SQL = text("""
    UPDATE wiki_pages
    SET title = COALESCE(:title, title),
        description = COALESCE(:description, description),
        body = CASE WHEN :body_provided THEN :body ELSE body END,
        body_status = CASE WHEN :body_provided THEN 'completed' ELSE body_status END,
        body_model = CASE WHEN :body_provided THEN 'user' ELSE body_model END,
        body_prompt_version = CASE WHEN :body_provided THEN 'manual' ELSE body_prompt_version END,
        body_generated_at = CASE WHEN :body_provided THEN now() ELSE body_generated_at END
    WHERE id = :page_id
""")

_INSERT_MANUAL_VERSION_SQL = text("""
    INSERT INTO wiki_page_versions (
        page_id, version_number, body, body_model, body_prompt_version,
        agent_run_id, trigger_reason
    ) VALUES (
        :page_id, :version_number, :body, 'user', 'manual',
        NULL, 'manual_edit'
    )
""")


@router.patch("/{slug}", response_model=WikiPageDetail)
async def edit_wiki_page(
    slug: str,
    payload: WikiPageEditRequest,
    session: AsyncSession = Depends(get_session),
) -> WikiPageDetail:
    """사용자 수동 편집 — title / description / body 각각 optional 변경.

    body 변경 시:
      - body_status='completed', body_model='user', body_prompt_version='manual'
      - 새 wiki_page_versions row (trigger_reason='manual_edit', 학습 신호 보존)
      - Qdrant linkmind_wiki_pages 의 body embedding 재upsert
    title/description 만 변경 시: 위 절차 생략 (body 안 바뀜).

    셋 다 None 이면 400.
    """
    has_title = payload.title is not None
    has_desc = payload.description is not None
    has_body = payload.body is not None
    if not (has_title or has_desc or has_body):
        raise HTTPException(
            status_code=400,
            detail="title / description / body 중 적어도 하나는 제공해야 합니다.",
        )

    row = (await session.execute(_FETCH_PAGE_FULL_SQL, {"slug": slug})).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail=f"wiki page not found: slug={slug}")
    page_id = row["id"]

    # title / description / body UPDATE (body 면 status/model 까지 manual 마킹)
    await session.execute(_UPDATE_PAGE_FIELDS_SQL, {
        "page_id": str(page_id),
        "title": payload.title,
        "description": payload.description,
        "body": payload.body or "",          # CASE WHEN 안 가서 무방
        "body_provided": has_body,
    })

    # body 변경 시 version 적립 + Qdrant 재upsert
    if has_body:
        latest = (await session.execute(
            _LATEST_VERSION_NUMBER_SQL, {"page_id": str(page_id)},
        )).scalar() or 0
        new_version = int(latest) + 1
        await session.execute(_INSERT_MANUAL_VERSION_SQL, {
            "page_id": str(page_id),
            "version_number": new_version,
            "body": payload.body,
        })

        # Qdrant body embedding 재upsert — 실패해도 DB 변경은 보존 (writer 와 동일 정책)
        try:
            embedder = get_embedding_provider()
            await ensure_wiki_collection(dim=embedder.dim)
            emb_result = await embedder.embed([payload.body])
            await upsert_wiki_page(
                page_id=str(page_id),
                vector=emb_result.vectors[0],
                payload={
                    "slug": row["slug"],
                    "title": payload.title if has_title else row["title"],
                    "description": payload.description if has_desc else row.get("description"),
                    "source_count": 0,    # 별 query 없이 — sources 는 detail GET 때 채워짐
                    "body_status": "ready",
                    "is_pinned": bool(row.get("is_pinned", False)),
                    "version_number": new_version,
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Qdrant wiki_pages upsert 실패 (수동 편집, slug=%s, 계속): %s",
                slug, exc,
            )

    await session.commit()
    logger.info(
        "wiki page 수동 편집 — slug=%s, title=%s, description=%s, body=%s",
        slug,
        "변경" if has_title else "유지",
        "변경" if has_desc else "유지",
        "변경" if has_body else "유지",
    )

    # 응답: 최신 wiki_context 다시 build (latest_version / sources / cross_links 채움)
    wiki_context = await _build_wiki_context(session, page_id)
    return await _wiki_context_to_response(wiki_context)


# ────────────────────────────────────────────────────────────────
# DELETE /wiki/{slug} — wiki + 연결 items 영구 삭제 (2026-05-27)
# ────────────────────────────────────────────────────────────────
#
# 사용자 의도: 자료가 깨진 (live URL X / 영상 삭제 / 도메인 죽음 등) 경우 wiki
# 페이지와 raw items 둘 다 영구 제거 → 새 텔레그램 입력으로 다시 채울 수 있게.
#
# 절차:
#   1. wiki_page_items 의 모든 item_id 조회
#   2. 각 item: Qdrant chunks 삭제 + Postgres items DELETE
#      → ON DELETE CASCADE 가 wiki_page_items 양방향 매핑 자동 정리.
#      → 다른 wiki 에 link 된 같은 item 도 함께 사라짐 (sources 에서 빠짐).
#   3. wiki_pages row DELETE — Qdrant wiki body point 도 delete.
#   4. 영향받은 다른 wiki 수 집계 (응답용).
#
# 보존: volumes/archive 의 raw 파일 (attachments.file_hash) — SHA-256 dedup 라
# 다른 item 이 같은 file_hash 참조 가능. orphan cleanup 은 별도 job.

_FETCH_PAGE_ITEM_IDS_SQL = text("""
    SELECT item_id FROM wiki_page_items WHERE wiki_page_id = :page_id
""")

_COUNT_AFFECTED_OTHER_WIKIS_SQL = text("""
    SELECT COUNT(DISTINCT wpi.wiki_page_id)
    FROM wiki_page_items wpi
    WHERE wpi.item_id = ANY(:item_ids)
      AND wpi.wiki_page_id != :this_page_id
""")


@router.delete("/{slug}", response_model=WikiPageDeleteResponse)
async def delete_wiki_page(
    slug: str,
    session: AsyncSession = Depends(get_session),
) -> WikiPageDeleteResponse:
    """wiki page + 연결 items (raw DB) 영구 삭제. irreversible.

    사용자가 frontend 의 2단계 confirm 통과 시 호출. 응답에 영향받은 다른 wiki
    수 포함 — 사용자가 사후 인지 가능.

    §11 Privacy §4 (삭제 권리, GDPR/PIPA) 부합 — §2 raw-first 원칙은 "ingest
    시점 무손실 보존" 의미라 사용자 명시 삭제와 충돌 X.
    """
    row = (await session.execute(_FETCH_PAGE_FULL_SQL, {"slug": slug})).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail=f"wiki page not found: slug={slug}")
    page_id = row["id"]

    # 1. 연결된 item_ids 조회
    item_id_rows = (await session.execute(
        _FETCH_PAGE_ITEM_IDS_SQL, {"page_id": str(page_id)},
    )).all()
    item_ids = [str(r[0]) for r in item_id_rows]

    # 2. 영향받을 다른 wiki 수 (item 삭제 전에 집계)
    affected_other_wikis = 0
    if item_ids:
        affected_other_wikis = int(
            (await session.execute(
                _COUNT_AFFECTED_OTHER_WIKIS_SQL,
                {"item_ids": item_ids, "this_page_id": str(page_id)},
            )).scalar() or 0
        )

    # 3. 각 item: Qdrant chunks 삭제 + Postgres DELETE (CASCADE 양방향 자동 정리)
    qdrant_items_status_sum = 0
    for iid in item_ids:
        try:
            status_code = await delete_chunks_for_item(iid)
            qdrant_items_status_sum += status_code
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Qdrant chunks 삭제 실패 (item=%s, 계속): %s", iid, exc,
            )
            qdrant_items_status_sum -= 1
        await session.execute(
            text("DELETE FROM items WHERE id = :id"), {"id": iid},
        )

    # 4. wiki_pages row DELETE (wiki_page_items 잔여 CASCADE + agent_runs SET NULL)
    await session.execute(
        text("DELETE FROM wiki_pages WHERE id = :id"), {"id": str(page_id)},
    )
    await session.commit()

    # 5. Qdrant wiki body point 삭제
    try:
        qdrant_wiki_status = await qdrant_delete_wiki_page(str(page_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Qdrant wiki page 삭제 실패 (slug=%s): %s", slug, exc)
        qdrant_wiki_status = -1

    logger.info(
        "wiki page 영구 삭제 — slug=%s, items=%d, affected_other_wikis=%d",
        slug, len(item_ids), affected_other_wikis,
    )
    return WikiPageDeleteResponse(
        deleted_wiki_slug=slug,
        deleted_wiki_page_id=page_id,
        deleted_items_count=len(item_ids),
        affected_other_wikis_count=affected_other_wikis,
        qdrant_wiki_status=qdrant_wiki_status,
        qdrant_items_status_sum=qdrant_items_status_sum,
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
# GET /wiki/_meta/stats — body_status 별 count (2026-05-27)
# ────────────────────────────────────────────────────────────────
#
# wiki list 의 status tab UI 가 mount 시 한 번 + 일괄 합성 중 polling.
# 가벼운 GROUP BY query (status 4종 + total).
#
# path 가 2-segment (`/_meta/stats`) 인 이유: `/_stats` 는 1-segment 라 `/{slug}` 와
# FastAPI route 매칭 충돌 (slug='_stats' 로 해석됨). `/_keywords/search` 와 같은
# 패턴 — internal 자원은 `_` prefix + 2-segment 로 통일.

_WIKI_STATS_SQL = text("""
    SELECT body_status, COUNT(*) AS n
    FROM wiki_pages
    GROUP BY body_status
""")


@router.get("/_meta/stats", response_model=WikiStatsResponse)
async def wiki_stats(
    session: AsyncSession = Depends(get_session),
) -> WikiStatsResponse:
    """wiki_pages 의 body_status 별 개수 + 합계."""
    rows = (await session.execute(_WIKI_STATS_SQL)).mappings().all()
    counts: dict[str, int] = {r["body_status"]: int(r["n"]) for r in rows}
    return WikiStatsResponse(
        issues=counts.get("issues", 0),
        pending=counts.get("pending", 0),
        completed=counts.get("completed", 0),
        total=sum(counts.values()),
    )


# ────────────────────────────────────────────────────────────────
# POST /wiki/_meta/batch_regenerate — empty/stale 일괄 합성 (2026-05-27)
# ────────────────────────────────────────────────────────────────
#
# path 가 `/_meta/batch_regenerate` 인 이유: `/_batch/regenerate` 는 2-segment
# 라도 `/{slug}/regenerate` (POST) 와 매칭 충돌 (slug='_batch'). `_meta` 하위로
# 묶어서 `/_meta/stats` 와 일관된 internal namespace.
#
# wiki_writer_batch CLI 와 동일 효과를 HTTP 로. fire-and-forget — request 즉시
# 응답, BackgroundTask 가 비동기 처리. frontend 가 GET /wiki/_stats polling 으로
# 진행 확인.
#
# vLLM 부하 — concurrency 4 (batch CLI 와 동일 — vLLM continuous batching 활용).
# 사용자 wiki 클릭 (eager GET /wiki/{slug}) 도 같은 vLLM queue 라 약간 양보됨.

_FETCH_BATCH_TARGETS_SQL = text("""
    SELECT id, slug, title
    FROM wiki_pages wp
    WHERE body_status = :status
      AND EXISTS (
          SELECT 1 FROM wiki_page_items wpi
          WHERE wpi.wiki_page_id = wp.id
            AND (wpi.user_action IS NULL OR wpi.user_action != 'removed')
      )
    ORDER BY is_pinned DESC, updated_at ASC
    LIMIT :limit
    FOR UPDATE SKIP LOCKED
""")


async def _batch_regenerate_worker(pages: list[dict]) -> None:
    """BackgroundTask 본체 — N 개 page 를 concurrency 4 로 합성.

    _process_one (wiki_writer_worker) 재사용. SKIP LOCKED row fetch 라 race-free.
    """
    from backend.jobs.wiki_writer_worker import _process_one

    if not pages:
        return

    sem = asyncio.Semaphore(4)

    async def _one(page: dict) -> bool:
        async with sem:
            return await _process_one(page)

    logger.info(
        "wiki batch regenerate 시작 — N=%d, concurrency=4", len(pages),
    )
    results = await asyncio.gather(*(_one(p) for p in pages), return_exceptions=True)
    ok = sum(1 for r in results if r is True)
    logger.info(
        "wiki batch regenerate 완료 — %d/%d 성공", ok, len(pages),
    )


@router.post("/_meta/batch_regenerate", response_model=WikiBatchRegenerateResponse)
async def wiki_batch_regenerate(
    payload: WikiBatchRegenerateRequest,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> WikiBatchRegenerateResponse:
    """body_status='issues' (또는 'pending') wiki_pages 일괄 합성.

    fire-and-forget. 처리 대상 fetch + 'pending' 마킹은 즉시 (session.commit),
    실제 LLM 합성은 BackgroundTask 가 async. frontend 가 stats polling 으로 진행 확인.
    """
    # 2026-05-27 통일: 3 status (ready/pending/completed) — batch 는 ready/pending
    # 만 처리 (completed 는 이미 끝).
    if payload.status not in ("issues", "pending"):
        raise HTTPException(
            status_code=400,
            detail="status 는 'issues' 또는 'pending' 만 지원 (현재: {})".format(payload.status),
        )

    rows = (await session.execute(
        _FETCH_BATCH_TARGETS_SQL,
        {"status": payload.status, "limit": payload.limit},
    )).mappings().all()
    pages = [dict(r) for r in rows]

    # status='pending' 으로 즉시 마킹 → frontend 의 stats polling 이 바로 변화 감지 +
    # 동시 합성 방지 (다른 트리거 — daemon 등 — 이 같은 page 안 잡음).
    if pages:
        await session.execute(
            text("""
                UPDATE wiki_pages SET body_status = 'pending'
                WHERE id = ANY(:ids)
            """),
            {"ids": [p["id"] for p in pages]},
        )
        await session.commit()

    # BackgroundTask 로 dispatch (request 응답은 즉시)
    background.add_task(_batch_regenerate_worker, pages)

    # page 당 ≈ 4초, concurrency 4 → N/4 * 4초
    estimated_seconds = max(4, (len(pages) + 3) // 4 * 4)

    logger.info(
        "wiki batch regenerate dispatched — status=%s, N=%d, ETA=%ds",
        payload.status, len(pages), estimated_seconds,
    )
    return WikiBatchRegenerateResponse(
        status=payload.status,
        dispatched=len(pages),
        estimated_seconds=estimated_seconds,
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
