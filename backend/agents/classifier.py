"""
ClassifierAgent — item 1개를 여러 wiki 페이지에 자동 분류 (M:N).

설계 (docs/llm_wiki_design.md §8):
  - input: ctx.related_item_id
  - 후보 wiki_pages retrieve:
    * wave-1d (지금): 모든 wiki_pages 의 title + description 을 후보 (수십~수백 OK)
    * wave-1f 후: Qdrant wiki_pages 컬렉션의 embedding top-K (수천 wiki_pages 확장)
  - LLM 호출 (JSON output, parse retry):
    * prompts/classifier_v1.yaml
    * matched (기존 페이지 매칭, list) + new_pages (새 페이지 제안, list)
  - DB mutation:
    * matched (confidence ≥ threshold) → wiki_page_items INSERT (ON CONFLICT 시 UPDATE)
    * new_pages → wiki_pages INSERT + wiki_page_items 즉시 link (confidence=1.0)
    * 매칭된 모든 wiki_pages.body_status = 'pending' (eager 합성 trigger)

state-centric — agent 내부 상태 X. 매 호출이 fresh.

§11 Privacy: classifier 가 사용자 데이터를 학습 X — vLLM base 모델 inference 만.
Phase 4 LoRA 학습 시 agent_runs.input_full / output_meta 가 학습 신호.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.base import AgentBase, AgentContext, load_prompt
from backend.llm.base import ChatMessage
from backend.llm.factory import get_llm_provider

logger = logging.getLogger("linkmind.agents.classifier")


_FETCH_ITEM_SQL = text("""
    SELECT id, source_type, title, source_url, tags, summary, user_notes,
           SUBSTRING(raw_content, 1, 1000) AS raw_preview
    FROM items WHERE id = :item_id
""")


_FETCH_CANDIDATE_PAGES_SQL = text("""
    SELECT id, slug, title, description, variant
    FROM wiki_pages
    ORDER BY updated_at DESC
    LIMIT :limit
""")


_UPSERT_WIKI_LINK_SQL = text("""
    INSERT INTO wiki_page_items (wiki_page_id, item_id, confidence, source, role)
    VALUES (:page_id, :item_id, :confidence, 'auto', :role)
    ON CONFLICT (wiki_page_id, item_id) DO UPDATE
        SET confidence = EXCLUDED.confidence,
            role = EXCLUDED.role
        WHERE wiki_page_items.user_action IS NULL  -- 사용자 override 보존
""")


_CREATE_WIKI_PAGE_SQL = text("""
    INSERT INTO wiki_pages (slug, title, description, body_status)
    VALUES (:slug, :title, :description, 'ready')
    -- 2026-05-27 사용자 명시: 신규 자료 ingest 직후 'ready' 단계 거치고 → daemon
    -- 이 fetch 시 'pending' 으로 변경 (writer 의 _MARK_GENERATING_SQL) → writer
    -- 완료 시 'completed'. 즉 사용자 mental: ready(들어옴) → pending(처리중) →
    -- completed(완료).
    -- daemon 의 fetch SQL 이 ANY(['ready','pending']) 매칭하므로 default 'ready'
    -- 라도 즉시 자동 처리 (~30초 안). 그 잠시 동안 ready 탭에서 새 자료 확인 가능.
    ON CONFLICT (slug) DO UPDATE SET slug = EXCLUDED.slug
    RETURNING id, slug
""")


_MARK_STALE_SQL = text("""
    UPDATE wiki_pages SET body_status = 'pending'
    WHERE id = ANY(:page_ids) AND body_status IN ('completed', 'ready')
""")


# JSON output 의 parse 시도 — LLM 이 코드 fence 등으로 감쌀 수 있음
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


class ClassifierAgent(AgentBase):
    """item → wiki_pages (M:N) 자동 분류."""

    agent_name = "classifier"
    agent_version = "v1"
    llm_model: str | None = None

    # 매칭 threshold (이 미만은 link 안 함). 사용자 override 가능 — ctx.extra 통해
    DEFAULT_THRESHOLD = 0.5
    # 후보 wiki_pages 갯수 한계 (wave-1d 임시 — wave-1f 후 Qdrant top-K 로)
    MAX_CANDIDATES = 30
    # JSON parse retry 횟수
    MAX_JSON_RETRY = 2

    _prompt: dict[str, Any] | None = None

    def _load_prompt(self) -> dict[str, Any]:
        if self._prompt is None:
            self._prompt = load_prompt("classifier", self.agent_version)
        return self._prompt

    async def build_context(self, ctx: AgentContext) -> dict[str, Any]:
        if not ctx.related_item_id:
            raise ValueError("ClassifierAgent: related_item_id 필수")

        session = ctx.session
        item_row = (await session.execute(
            _FETCH_ITEM_SQL, {"item_id": str(ctx.related_item_id)},
        )).mappings().first()
        if not item_row:
            raise LookupError(f"items 에 id={ctx.related_item_id} 없음")

        # wave-1f (2026-05-27): Qdrant linkmind_wiki_pages top-K 의미 검색으로 후보 추출.
        # 옛 wave-1d 의 ORDER BY updated_at DESC LIMIT 30 은 23k wiki 중 최근 30 만 — 새
        # item 의 의미 매칭이 거의 fail. item 의 title+summary 를 bge-m3 로 embed → Qdrant
        # cosine top-K → 후보 wiki_pages. Qdrant 실패하면 fallback 으로 옛 방식.
        query_text = " ".join(
            filter(None, [
                item_row.get("title") or "",
                item_row.get("summary") or "",
            ])
        )[:2000].strip()

        candidates: list[Any] = []
        if query_text:
            try:
                from backend.embedding.factory import get_embedding_provider
                from backend.embedding.wiki_qdrant import (
                    ensure_wiki_collection,
                    search_wiki_pages,
                )

                embedder = get_embedding_provider()
                await ensure_wiki_collection(dim=embedder.dim)
                emb = await embedder.embed([query_text])
                points = await search_wiki_pages(
                    query_vector=emb.vectors[0],
                    top_k=self.MAX_CANDIDATES,
                )
                ids = [str(p.id) for p in points]
                if ids:
                    rows = (await session.execute(
                        text("""
                            SELECT id, slug, title, description, variant
                            FROM wiki_pages WHERE id = ANY(:ids)
                        """),
                        {"ids": ids},
                    )).mappings().all()
                    # Qdrant score 순서 유지 (id list 순서)
                    id_to_row = {str(r["id"]): r for r in rows}
                    candidates = [id_to_row[i] for i in ids if i in id_to_row]
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Qdrant wiki candidate 실패 (item=%s) — fallback ORDER BY updated_at: %s",
                    ctx.related_item_id, exc,
                )

        # fallback — Qdrant 빈 결과 / 실패 / item 의 query_text 없을 때
        if not candidates:
            candidates = (await session.execute(
                _FETCH_CANDIDATE_PAGES_SQL, {"limit": self.MAX_CANDIDATES},
            )).mappings().all()

        return {
            "item_id": str(ctx.related_item_id),
            "item": {
                "id": str(item_row["id"]),
                "source_type": item_row["source_type"],
                "title": item_row["title"],
                "source_url": item_row["source_url"],
                "tags": list(item_row["tags"] or []),
                "summary": item_row["summary"],
                "user_notes": item_row["user_notes"],
                "raw_preview": item_row["raw_preview"],
            },
            "candidates": [
                {
                    "id": str(c["id"]),
                    "slug": c["slug"],
                    "title": c["title"],
                    "description": c["description"],
                    "variant": c["variant"],
                }
                for c in candidates
            ],
            "threshold": float(ctx.extra.get("threshold", self.DEFAULT_THRESHOLD)),
        }

    async def invoke(self, input_payload: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
        assert self._ctx is not None
        session: AsyncSession = self._ctx.session

        item_id = UUID(input_payload["item_id"])
        item = input_payload["item"]
        candidates = input_payload["candidates"]
        threshold = input_payload["threshold"]

        prompt = self._load_prompt()
        user_msg = _build_classifier_user_msg(prompt["user_template"], item, candidates)

        provider = get_llm_provider()
        parsed: dict[str, Any] | None = None
        last_text: str = ""
        last_error: str | None = None

        for attempt in range(self.MAX_JSON_RETRY + 1):
            llm_resp = await provider.chat(
                messages=[
                    ChatMessage(role="system", content=prompt["system"]),
                    ChatMessage(role="user", content=user_msg),
                ],
                model=self.llm_model,
                temperature=0.1,    # JSON 안정
                max_tokens=1024,
            )
            last_text = llm_resp.text.strip()
            parsed = _try_parse_json(last_text)
            if parsed is not None:
                break
            last_error = f"JSON parse 실패 (attempt {attempt + 1}): {last_text[:200]}"
            logger.warning(last_error)

        if parsed is None:
            raise RuntimeError(last_error or "JSON parse 실패")

        matched = parsed.get("matched") or []
        new_pages_proposals = parsed.get("new_pages") or []
        reasoning = parsed.get("reasoning")

        # ──────── DB mutation ────────
        linked_page_ids: list[str] = []
        skipped_low_conf: list[dict[str, Any]] = []

        # 1) matched (기존 페이지) — threshold 이상만 link
        slug_to_id: dict[str, str] = {c["slug"]: c["id"] for c in candidates}
        for m in matched:
            slug = m.get("wiki_slug")
            conf = float(m.get("confidence", 0))
            role = m.get("role")
            if not slug or slug not in slug_to_id:
                continue
            if conf < threshold:
                skipped_low_conf.append({"slug": slug, "confidence": conf})
                continue
            page_id = slug_to_id[slug]
            await session.execute(_UPSERT_WIKI_LINK_SQL, {
                "page_id": page_id,
                "item_id": str(item_id),
                "confidence": conf,
                "role": role,
            })
            linked_page_ids.append(page_id)

        # 2) new_pages — 신규 wiki_pages INSERT + link (confidence=1.0)
        created_pages: list[dict[str, Any]] = []
        for np in new_pages_proposals:
            slug = np.get("slug")
            title = np.get("title")
            description = np.get("description")
            if not slug or not title:
                continue
            row = (await session.execute(_CREATE_WIKI_PAGE_SQL, {
                "slug": slug,
                "title": title,
                "description": description,
            })).first()
            if not row:
                continue
            new_pid, new_slug = str(row[0]), row[1]
            await session.execute(_UPSERT_WIKI_LINK_SQL, {
                "page_id": new_pid,
                "item_id": str(item_id),
                "confidence": 1.0,
                "role": "primary",
            })
            linked_page_ids.append(new_pid)
            created_pages.append({"id": new_pid, "slug": new_slug, "title": title})

        # 3) 자기 1:1 fallback wiki — 매칭과 무관하게 항상 생성 (2026-05-27).
        #    사용자 명시: "내가 입력한 자료의 wiki 가 생성 안 되고 부수적인 wiki 만
        #    생성되는 게 무슨 의미가 있어?"
        #    → 모든 ingest 자료가 자기 wiki 페이지를 가짐 (검색 시 1순위로 그 자료
        #    찾을 수 있게). 매칭된 다른 wiki 는 부수적 cross-link.
        #    옛 wave-1g backfill 의 1:1 패턴 (`url__item__<uuid>`) 과 일관.
        self_slug = f"url__item__{item_id}"
        self_title = item.get("title") or self_slug
        self_desc = (item.get("summary") or "")[:500] or None
        self_row = (await session.execute(_CREATE_WIKI_PAGE_SQL, {
            "slug": self_slug,
            "title": self_title,
            "description": self_desc,
        })).first()
        self_wiki_created = False
        if self_row:
            self_pid = str(self_row[0])
            await session.execute(_UPSERT_WIKI_LINK_SQL, {
                "page_id": self_pid,
                "item_id": str(item_id),
                "confidence": 1.0,
                "role": "self",   # role='self' — 1:1 fallback wiki 표식
            })
            linked_page_ids.append(self_pid)
            self_wiki_created = True

        # 4) 매칭된 모든 wiki_pages.body_status = 'pending' (eager 합성 trigger)
        if linked_page_ids:
            await session.execute(_MARK_STALE_SQL, {"page_ids": linked_page_ids})

        output_meta = {
            "matched_count": len(matched),
            "new_pages_count": len(created_pages),
            "self_wiki_created": self_wiki_created,
            "linked_page_ids": linked_page_ids,
            "created_pages": created_pages,
            "skipped_low_conf": skipped_low_conf,
            "reasoning": reasoning,
            "raw_output": last_text,
        }
        summary = (
            f"item={item.get('title') or str(item_id)[:8]} → "
            f"matched={len(matched)} (linked={len(linked_page_ids) - len(created_pages) - (1 if self_wiki_created else 0)}), "
            f"new_pages={len(created_pages)}, self_wiki={self_wiki_created}"
        )
        return summary, output_meta


# ============================================================================
# helpers
# ============================================================================

def _try_parse_json(s: str) -> dict[str, Any] | None:
    """LLM 출력에서 JSON 추출. ```json fence``` 또는 raw {} 모두 처리."""
    s = s.strip()
    # 1) 코드 fence 시도
    m = _JSON_FENCE_RE.search(s)
    if m:
        s = m.group(1).strip()
    # 2) raw JSON
    try:
        data = json.loads(s)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    # 3) 첫 { 부터 마지막 } 까지 추출 시도 (LLM 이 가끔 앞뒤 잡담 붙임)
    start = s.find("{")
    end = s.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            data = json.loads(s[start:end + 1])
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    return None


def _build_classifier_user_msg(
    template: str, item: dict[str, Any], candidates: list[dict[str, Any]],
) -> str:
    """classifier_v1.yaml 의 user_template 변수 치환."""
    cand_lines: list[str] = []
    for c in candidates:
        desc = c.get("description") or "(no description)"
        cand_lines.append(f"- slug: {c['slug']}\n  title: {c['title']}\n  description: {desc[:200]}")
    candidates_block = "\n".join(cand_lines) if cand_lines else "(no existing wiki pages)"

    tags = item.get("tags") or []
    summary = (item.get("summary") or "(no summary)")[:600]
    user_notes = (item.get("user_notes") or "(no user notes)")[:300]
    raw_preview = (item.get("raw_preview") or "(no raw)")[:500]

    return template.format(
        item_id=item["id"],
        source_type=item.get("source_type") or "?",
        title=item.get("title") or "(no title)",
        source_url=item.get("source_url") or "(no url)",
        tags=" ".join(f"#{t}" for t in tags[:10]) if tags else "(no tags)",
        summary=summary,
        user_notes=user_notes,
        raw_preview=raw_preview,
        candidate_count=len(candidates),
        candidates_block=candidates_block,
    )
