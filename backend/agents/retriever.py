"""
RetrieverAgent — wiki page 의 source items 통합 (LLM 호출 X, DB 만).

설계:
  - input: ctx.related_wiki_page_id
  - DB 조회:
    - wiki_pages 메타 (title / description / body / variant)
    - wiki_page_items 로 link 된 모든 items (user_action='removed' 제외, confidence DESC)
      각 item 의 (title / summary / source_type / source_url / user_notes / tags)
    - attachments (한 item 당 첫 5개, multi-modality)
    - cross-link 후보 (같은 items 가 link 된 다른 wiki_pages — wave-2 에 cross-link
      진화에 활용)
  - output_text: 짧은 summary ("12 sources, 3 attachments, 2 cross-link candidates")
  - output_meta: 전체 WikiContext dict — writer agent 가 그대로 소비

state-centric — 매 호출이 fresh, 누적 X.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.base import AgentBase, AgentContext

logger = logging.getLogger("linkmind.agents.retriever")


# ============================================================================
# DB queries (this module 안에서만 사용 — repository.py 와 별 layer)
# ============================================================================

_FETCH_PAGE_SQL = text("""
    SELECT
        wp.id, wp.topic_id, wp.slug, wp.title, wp.description, wp.variant,
        wp.body, wp.body_status, wp.body_generated_at,
        wp.user_overrides, wp.is_pinned, wp.keywords,
        wp.created_at, wp.updated_at,
        (SELECT COALESCE(MAX(version_number), 0)
            FROM wiki_page_versions WHERE page_id = wp.id) AS latest_version
    FROM wiki_pages wp
    WHERE wp.id = :page_id
""")


_FETCH_SOURCES_SQL = text("""
    SELECT
        wpi.item_id, wpi.confidence, wpi.source, wpi.role, wpi.user_action,
        i.title, i.summary, i.source_type, i.source_url,
        i.user_notes, i.is_read,
        i.ingested_at, i.source_metadata
    FROM wiki_page_items wpi
    JOIN items i ON i.id = wpi.item_id
    WHERE wpi.wiki_page_id = :page_id
      AND (wpi.user_action IS NULL OR wpi.user_action != 'removed')
    ORDER BY wpi.confidence DESC, i.ingested_at DESC
""")


_FETCH_ATTACHMENTS_SQL = text("""
    SELECT
        a.id, a.item_id, a.file_path, a.file_hash, a.mime_type, a.role,
        a.caption, a.ai_description, a.width, a.height
    FROM attachments a
    WHERE a.item_id = ANY(:item_ids)
    ORDER BY a.created_at ASC
""")

# 위키 본문에 삽입할 figure — caption 이 제대로 있는 것만 (Docling 추출물). pymupdf
# 잔재(caption='page N')와 caption 없는 조각/로고는 제외. file_hash 로 /files/{hash}
# URL 구성. 우선순위 정렬은 Python(_figure_priority)에서 (아키텍처/결과 그림 먼저).
_FETCH_FIGURES_SQL = text("""
    SELECT a.file_hash, a.caption, a.width, a.height
    FROM attachments a
    WHERE a.item_id = ANY(:item_ids)
      AND a.role = 'figure'
      AND a.caption IS NOT NULL
      AND a.caption <> ''
      AND a.caption !~* '^page\\s+[0-9]'
      AND a.file_hash IS NOT NULL
    ORDER BY a.created_at ASC
""")


# figure caption 우선순위 키워드 (사용자 명시 2026-06-04: 모델/시스템 아키텍처 + 결과
# 그림 우선). caption 을 보고 0=아키텍처, 1=결과, 2=기타 로 분류해 본문 상위에 배치.
_FIG_ARCH_KW = (
    "architecture", "overview", "framework", "pipeline", "model",
    "system", "network", "diagram", "structure", "schematic",
)
_FIG_RESULT_KW = (
    "result", "qualitative", "quantitative", "comparison", "performance",
    "ablation", "accuracy", "benchmark", "evaluation", "experiment",
)


def _figure_priority(caption: str) -> int:
    c = (caption or "").lower()
    if any(k in c for k in _FIG_ARCH_KW):
        return 0
    if any(k in c for k in _FIG_RESULT_KW):
        return 1
    return 2


# 같은 items 가 다른 wiki_pages 에 link 된 cross-link 후보
_FETCH_CROSS_LINK_CANDIDATES_SQL = text("""
    SELECT
        wp.slug, wp.title, COUNT(*) AS shared_items
    FROM wiki_page_items wpi
    JOIN wiki_pages wp ON wp.id = wpi.wiki_page_id
    WHERE wpi.item_id = ANY(:item_ids)
      AND wpi.wiki_page_id != :page_id
      AND (wpi.user_action IS NULL OR wpi.user_action != 'removed')
    GROUP BY wp.slug, wp.title
    ORDER BY shared_items DESC
    LIMIT 10
""")


# ============================================================================
# RetrieverAgent
# ============================================================================

class RetrieverAgent(AgentBase):
    """wiki_page 의 source items + 첨부 + cross-link 후보를 fresh 조립."""

    agent_name = "retriever"
    agent_version = "v1"
    llm_model = None    # LLM 안 부름 — 순수 DB

    # 첨부는 한 item 당 N 개만 (context window 보호)
    MAX_ATTACHMENTS_PER_ITEM = 5
    # 위키 본문에 삽입할 figure 상한 (너무 많으면 위키가 산만). 우선순위 정렬 후 상위 N.
    MAX_FIGURES_IN_BODY = 10

    async def build_context(self, ctx: AgentContext) -> dict[str, Any]:
        if not ctx.related_wiki_page_id:
            raise ValueError("RetrieverAgent: related_wiki_page_id 필수")
        return {"wiki_page_id": str(ctx.related_wiki_page_id)}

    async def invoke(self, input_payload: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
        page_id = UUID(input_payload["wiki_page_id"])
        assert self._ctx is not None
        session: AsyncSession = self._ctx.session

        wiki_context = await _build_wiki_context(session, page_id)

        summary = (
            f"{len(wiki_context['sources'])} sources, "
            f"{wiki_context['attachment_count']} attachments, "
            f"{len(wiki_context['cross_link_candidates'])} cross-link candidates"
        )
        return summary, wiki_context


# ============================================================================
# 핵심 builder — agent 외부에서도 직접 호출 가능 (writer 가 invoke 안에서 사용)
# ============================================================================

async def _build_wiki_context(
    session: AsyncSession, page_id: UUID,
) -> dict[str, Any]:
    """page_id 의 fresh wiki context 조립 — writer agent 가 소비할 형태.

    반환 dict 스키마:
        {
          "page": { id, slug, title, description, variant, body, body_status,
                    latest_version, is_pinned, user_overrides },
          "sources": [
            { item_id, title, summary, source_type, source_url,
              confidence, role, tags, user_notes, attachments: [...] }
          ],
          "attachment_count": int,
          "cross_link_candidates": [ { slug, title, shared_items } ],
          "user_notes_combined": str,    # 모든 source 의 user_notes 합본
        }
    """
    page_row = (await session.execute(_FETCH_PAGE_SQL, {"page_id": page_id})).mappings().first()
    if not page_row:
        raise LookupError(f"wiki_pages 에 id={page_id} 없음")

    src_rows = (await session.execute(_FETCH_SOURCES_SQL, {"page_id": page_id})).mappings().all()
    item_ids = [str(r["item_id"]) for r in src_rows]

    # 첨부 조회 (item 단위 그루핑)
    attachments_by_item: dict[str, list[dict[str, Any]]] = {}
    attachment_count = 0
    if item_ids:
        att_rows = (await session.execute(
            _FETCH_ATTACHMENTS_SQL, {"item_ids": item_ids},
        )).mappings().all()
        for a in att_rows:
            iid = str(a["item_id"])
            lst = attachments_by_item.setdefault(iid, [])
            if len(lst) >= RetrieverAgent.MAX_ATTACHMENTS_PER_ITEM:
                continue
            lst.append({
                "id": str(a["id"]),
                "file_path": a["file_path"],
                "file_hash": a["file_hash"],
                "mime_type": a["mime_type"],
                "role": a["role"],
                "caption": a["caption"],
                "ai_description": a["ai_description"],
                "width": a["width"],
                "height": a["height"],
            })
            attachment_count += 1

    # 위키 본문 삽입용 figure 수집 (attachments 5개 제한과 별개) — caption 있는 Docling
    # figure 만, 아키텍처/결과 그림 우선 정렬 후 상한. file_hash 로 /files/{hash} URL.
    figures: list[dict[str, Any]] = []
    if item_ids:
        fig_rows = (await session.execute(
            _FETCH_FIGURES_SQL, {"item_ids": item_ids},
        )).mappings().all()
        seen_hashes: set[str] = set()
        collected: list[dict[str, Any]] = []
        for f in fig_rows:
            fh = f["file_hash"]
            if not fh or fh in seen_hashes:
                continue
            seen_hashes.add(fh)
            collected.append({
                "file_hash": fh,
                "caption": (f["caption"] or "").strip(),
                "width": f["width"],
                "height": f["height"],
            })
        # 아키텍처(0) → 결과(1) → 기타(2) 우선, 원래 순서 보존(stable). 상한 적용.
        collected.sort(key=lambda x: _figure_priority(x["caption"]))
        figures = collected[: RetrieverAgent.MAX_FIGURES_IN_BODY]

    # cross-link 후보
    cross_links: list[dict[str, Any]] = []
    if item_ids:
        cl_rows = (await session.execute(
            _FETCH_CROSS_LINK_CANDIDATES_SQL,
            {"item_ids": item_ids, "page_id": page_id},
        )).mappings().all()
        cross_links = [
            {"slug": r["slug"], "title": r["title"], "shared_items": int(r["shared_items"])}
            for r in cl_rows
        ]

    # source list 조립 + user_notes 합본
    sources: list[dict[str, Any]] = []
    notes_chunks: list[str] = []
    for r in src_rows:
        iid = str(r["item_id"])
        sources.append({
            "item_id": iid,
            "title": r["title"],
            "summary": r["summary"],
            "source_type": r["source_type"],
            "source_url": r["source_url"],
            "confidence": float(r["confidence"]) if r["confidence"] is not None else None,
            "role": r["role"],
            "user_notes": r["user_notes"],
            "is_read": bool(r["is_read"]),
            "ingested_at": r["ingested_at"].isoformat() if r["ingested_at"] else None,
            "attachments": attachments_by_item.get(iid, []),
        })
        if r["user_notes"]:
            notes_chunks.append(f"[{r['title'] or iid[:8]}]\n{r['user_notes']}")

    return {
        "page": {
            "id": str(page_row["id"]),
            "topic_id": str(page_row["topic_id"]) if page_row["topic_id"] else None,
            "slug": page_row["slug"],
            "title": page_row["title"],
            "description": page_row["description"],
            "variant": page_row["variant"],
            "body": page_row["body"],
            "body_status": page_row["body_status"],
            "body_generated_at": (
                page_row["body_generated_at"].isoformat()
                if page_row["body_generated_at"] else None
            ),
            "latest_version": int(page_row["latest_version"] or 0),
            "is_pinned": bool(page_row["is_pinned"]),
            "user_overrides": page_row["user_overrides"],
            "keywords": list(page_row["keywords"] or []),
            "created_at": (
                page_row["created_at"].isoformat()
                if page_row["created_at"] else None
            ),
            "updated_at": (
                page_row["updated_at"].isoformat()
                if page_row["updated_at"] else None
            ),
        },
        "sources": sources,
        "attachment_count": attachment_count,
        "figures": figures,
        "cross_link_candidates": cross_links,
        "user_notes_combined": "\n\n".join(notes_chunks),
    }
