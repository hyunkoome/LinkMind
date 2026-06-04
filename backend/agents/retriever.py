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
    SELECT a.file_hash, a.caption, a.width, a.height, a.item_id
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


# ── 문서 타입 판별 (재설계 element A/F) ──
# 논문류(arxiv/pdf)면 writer 가 논문 구조 prompt + raw 본문 + 논문 제목 override 를 쓴다.
# 그 외(url/youtube/github/slack/telegram)는 기존 개념형 구조.
_PAPER_SOURCE_TYPES = {"arxiv", "pdf"}
_SELF_WIKI_SLUG_PREFIX = "url__item__"


def _is_paper_source(source: dict[str, Any]) -> bool:
    """source 가 논문류인지 — source_type 기준 (arxiv/pdf). Docling 추출이면 더 좋지만
    pypdf 추출 본문도 raw 로 유용하므로 source_type 만으로 판단."""
    return (source.get("source_type") or "") in _PAPER_SOURCE_TYPES


def _self_wiki_identity_item_id(slug: str) -> str | None:
    """self-wiki(url__item__<uuid>) slug 에서 정체성 item uuid 추출. 아니면 None.

    self-wiki 는 특정 1개 자료의 전용 페이지다. 그 자료(=정체성 item)가 무엇이냐로
    doc_type/제목을 정해야 한다 — cross-link 으로 끌려온 논문(PDF)에 제목/구조가
    납치되지 않게 (예: NVIDIA 블로그 self-wiki 가 cross-link 된 cosmos PDF 제목을 다는 문제).
    """
    if not slug or not slug.startswith(_SELF_WIKI_SLUG_PREFIX):
        return None
    return slug[len(_SELF_WIKI_SLUG_PREFIX):] or None


# raw 발췌 길이 — Gemma 16384 token context. head(앞부분: 제목/초록/서론/방법/실험)
# 위주로 크게 잡아 writer 가 원문 없이도 충실한 위키를 쓸 재료를 충분히 주고(사용자
# 요구: "원문 안 봐도 파악될 정도"), 뒤쪽 표(실험 결과)는 따로 긁어 붙여 element E
# (table)가 잘려나가지 않게 한다. 영어 위주라 char→token 비율이 낮아 20000자 ≈ ~6000
# 토큰. system+sources(~2000) + output(7168) 합쳐도 16384 안에 들어옴.
RAW_HEAD_CHARS = 20000      # 앞에서부터 통째 발췌 (논문 핵심 서술)
RAW_MAX_CHARS = 26000       # head + 추가 table 포함 총 상한


def _extract_markdown_tables(text_md: str) -> list[str]:
    """markdown 본문에서 표 블록(연속된 '|...|' 줄 묶음)을 추출. Docling 이 raw_content
    에 markdown table 로 보존한 실험 수치표(element E)를 head 발췌 뒤에 따로 붙이기 위함.

    표는 보통 논문 중후반(실험 섹션)이라 head 발췌에서 잘릴 수 있어 전 구간을 훑는다.
    셀 데이터는 그대로(영어/숫자 보존) — 번역 금지(수치 정확성). 캡션 한글요약은 writer 담당.
    """
    tables: list[str] = []
    current: list[str] = []
    for line in text_md.split("\n"):
        s = line.strip()
        if s.startswith("|") and s.endswith("|") and s.count("|") >= 2:
            current.append(line)
        else:
            if len(current) >= 2:        # 헤더+구분선 최소 2줄이어야 표
                tables.append("\n".join(current))
            current = []
    if len(current) >= 2:
        tables.append("\n".join(current))
    return tables


def _build_raw_excerpt(raw: str) -> str:
    """primary 논문 raw markdown 을 token 예산 안에서 발췌. head 통째 + 그 뒤 구간의
    표를 따로 붙임 (table 잘림 방지, element E). 이미 head 안에 있는 표는 중복 제외."""
    if not raw:
        return ""
    raw = raw.strip()
    head = raw[:RAW_HEAD_CHARS]
    if len(raw) <= RAW_HEAD_CHARS:
        return head
    # head 뒤 구간의 표만 추가 (head 에 이미 등장한 표는 head 에 포함됐으니 제외)
    tail_tables = _extract_markdown_tables(raw[RAW_HEAD_CHARS:])
    out = head
    budget = RAW_MAX_CHARS - len(head)
    appended: list[str] = []
    for tbl in tail_tables:
        if budget <= 0:
            break
        block = "\n\n" + tbl
        appended.append(block[:budget])
        budget -= len(block)
    if appended:
        out += "\n\n[... 중략 — 이하 본문 뒷부분의 표 발췌 ...]" + "".join(appended)
    else:
        out += "\n\n[... 이하 생략 ...]"
    return out


# primary source(논문)의 raw markdown 본문 — writer 가 summary 가 아닌 실제 본문 기반으로
# 합성하기 위해 (재설계 element B). Docling 이 raw_content 에 넣은 풍부한 markdown(섹션/표
# 보존)을 한 source 만 한정해서 가져온다 (전 source 의 raw 를 context 에 실으면 agent_runs
# output_meta JSONB 가 비대해짐 — primary 1개만 발췌). 발췌·table 보존은 Python 에서.
_FETCH_ITEM_RAW_SQL = text("""
    SELECT raw_content FROM items WHERE id = :item_id
""")


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

    # self-wiki(url__item__<uuid>)의 정체성 item — 그림/제목/본문을 이 자료 중심으로 (de-clone).
    # 개념 wiki 면 None. 그림 수집(아래)에서 self-wiki 는 정체성 item 그림만 쓴다 — cross-link
    # 된 논문 PDF 의 그림이 블로그 self-wiki 에 딸려오던 문제 차단.
    identity_item_id = _self_wiki_identity_item_id(page_row["slug"])

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
        seen_captions: set[str] = set()    # 같은 캡션 중복 제거 (Docling multi-panel)
        collected: list[dict[str, Any]] = []
        for f in fig_rows:
            # self-wiki 면 정체성 item 의 그림만 (cross-link 논문 그림 배제, de-clone)
            if identity_item_id and str(f["item_id"]) != identity_item_id:
                continue
            fh = f["file_hash"]
            if not fh or fh in seen_hashes:
                continue
            cap = (f["caption"] or "").strip()
            # 같은 캡션을 가진 다른 file_hash 는 보통 Docling 이 한 figure 를 여러 조각으로
            # 쪼갠 것 — 위키에 "번호 다른데 캡션 같은 그림"이 2개씩 뜨는 원인. 캡션 기준으로도
            # dedup (첫 1개만, 우선순위 정렬 전이라 DB 순서 = page 순서). (2026-06-04)
            cap_key = cap.lower()
            if cap_key and cap_key in seen_captions:
                continue
            seen_hashes.add(fh)
            if cap_key:
                seen_captions.add(cap_key)
            collected.append({
                "file_hash": fh,
                "caption": cap,
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

    # ── 문서 타입 판별 + primary 논문 raw 발췌 (재설계 element A/B/F) ──
    # primary(논문 구조·raw 발췌·제목 override 의 기준) 선택:
    #   - self-wiki(url__item__<uuid>): 그 uuid = 정체성 item. 정체성 item 이 논문이면
    #     paper, 아니면 general (cross-link 된 논문 PDF 에 제목/구조 납치 방지).
    #   - 개념 wiki(kebab): 단일 정체성 item 이 없으니 sources 중 첫 논문류를 primary 로.
    # sources 는 confidence DESC 정렬 — 개념 wiki 에서 self url 이 [0], pdf 가 [1+] 여도 OK.
    doc_type = "general"
    primary_raw: dict[str, Any] | None = None
    identity_src = None
    if identity_item_id:
        identity_src = next(
            (s for s in sources if s["item_id"] == identity_item_id), None,
        )
    if identity_src is not None:
        # self-wiki: 정체성 item 기준 (논문일 때만 paper)
        primary = identity_src if _is_paper_source(identity_src) else None
    else:
        # 개념 wiki (또는 정체성 item 유실) → 첫 논문 source
        primary = next((s for s in sources if _is_paper_source(s)), None)
    if primary is not None:
        raw_row = (await session.execute(
            _FETCH_ITEM_RAW_SQL, {"item_id": primary["item_id"]},
        )).first()
        raw_full = raw_row[0] if raw_row else None
        # raw 가 충분히 길 때만 paper 구조로 (OG meta 만 있는 빈약한 pdf 는 일반 구조).
        if raw_full and len(raw_full.strip()) >= 400:
            doc_type = "paper"
            primary_raw = {
                "item_id": primary["item_id"],
                "title": primary.get("title"),
                "source_type": primary.get("source_type"),
                "source_url": primary.get("source_url"),
                "excerpt": _build_raw_excerpt(raw_full),
            }

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
        # 재설계: writer 가 논문/일반 구조를 분기하고 raw 본문 기반 합성을 하도록.
        "doc_type": doc_type,
        "primary_raw": primary_raw,
        # self-wiki(url__item__<uuid>) 의 정체성 item — 일반 writer 가 이 자료 중심으로
        # 본문/제목을 쓰고 cross-link 자료는 '관련 자료' 로만 다루게 (de-clone, 2026-06-04).
        # 개념 wiki 면 None (정체성 단일 item 없음).
        "identity_item": (
            {
                "item_id": identity_src["item_id"],
                "title": identity_src.get("title"),
                "source_type": identity_src.get("source_type"),
            }
            if identity_src is not None else None
        ),
    }
