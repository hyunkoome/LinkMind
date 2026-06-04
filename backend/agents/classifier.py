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
from backend.utils.external_ids import (
    extract_external_ids,
    first_url,
    primary_external_id,
)
from backend.utils.wiki_slug import sanitize_wiki_slug

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
    VALUES (:slug, :title, :description, 'pending')
    -- 2026-05-27 사용자 mental 명확화: 신규 ingest 자료는 'pending' 으로 바로
    -- 처리 큐 (daemon 자동 fetch). 'issues' (옛 'issues') 는 batch/writer fail
    -- reset / stuck 자료 만 누적 — 사용자가 issues 탭에서 일괄 재합성 가능.
    ON CONFLICT (slug) DO UPDATE SET slug = EXCLUDED.slug
    RETURNING id, slug
""")


# 2026-05-27: 'completed' 만 매칭 — 이미 합성된 wiki 가 새 item link 후 재합성
# 필요할 때만 'pending' 으로. 'issues' (방금 만든 self_wiki / new_page) 는 이미
# 처리 대기 상태라 그대로 둠. 옛 'issues' 매칭은 self_wiki 의 default 'issues' 를
# 즉시 'pending' 으로 덮어쓰는 버그 — 사용자가 ready tab 에서 새 자료 못 봤던
# 원인.
_MARK_STALE_SQL = text("""
    UPDATE wiki_pages SET body_status = 'pending'
    WHERE id = ANY(:page_ids) AND body_status = 'completed'
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
    # 자기 정체성 topic 의 최소 confidence (D10.6, 2026-05-28). auto_link_topics 가
    # 자료의 primary external_id (또는 fallback) 를 confidence=1.0 으로, cross-modal
    # 단서 (예: YouTube 설명란의 github 링크) 를 0.7 로 단다. 이 미만 (0.7 단서) 은
    # wiki 로 승격 안 함 — 중복 wiki 방지.
    IDENTITY_TOPIC_MIN_CONFIDENCE = 0.9
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
                # 후보 30개를 매칭하면 matched 배열이 길어진다. 1024 는 url__item__
                # <uuid> 같은 긴 slug 가 많을 때 중간에서 잘려 (truncation) JSON parse
                # 가 실패했다 (2026-06-02 텔레그램 ingest classifier hook linked=0 원인).
                max_tokens=2048,
            )
            last_text = llm_resp.text.strip()
            parsed = _try_parse_json(last_text)
            if parsed is not None:
                break
            last_error = f"JSON parse 실패 (attempt {attempt + 1}): {last_text[:200]}"
            logger.warning(last_error)

        # 모든 재시도가 truncation 으로 실패했으면 — 잘린 응답에서 완성된 matched
        # 요소라도 복구 (max_tokens 를 늘려도 후보가 더 많으면 또 잘릴 수 있으므로
        # resilience 로 둔다). 복구 실패 시에만 RuntimeError.
        if parsed is None:
            parsed = _salvage_truncated_json(last_text)
            if parsed is not None:
                logger.warning(
                    "classifier JSON truncated — salvage 로 matched %d개 복구",
                    len(parsed.get("matched") or []),
                )

        if parsed is None:
            raise RuntimeError(last_error or "JSON parse 실패")

        matched = parsed.get("matched") or []
        new_pages_proposals = parsed.get("new_pages") or []
        reasoning = parsed.get("reasoning")

        # ──────── DB mutation ────────
        linked_page_ids: list[str] = []
        skipped_low_conf: list[dict[str, Any]] = []

        # 1) matched (기존 페이지) — threshold 이상만 link
        # linked_to_concept: 이 item 이 self(url__item__) 가 아닌 '진짜' wiki(개념 kebab
        # 또는 외부 yt__/github__/arxiv__)에 연결됐는지. True 면 아래 3)에서 중복 self_wiki
        # 생성을 건너뛴다 (중복 위키 예방, 2026-06-04). 같은 논문이 개념 위키 + self_wiki
        # 로 쪼개지던 문제 차단 — item 은 이미 개념 위키에 살아있으니 self 는 불필요.
        linked_to_concept = False
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
            # 개념/외부 wiki(= self 아닌 것)에 매칭됐으면 self_wiki 불필요로 표시.
            if not slug.startswith("url__item__"):
                linked_to_concept = True

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
            # new_pages 는 LLM 이 제안한 개념(kebab) wiki — 이 item 의 정체성 home.
            # self_wiki 가 별도로 또 생기지 않도록 표시 (중복 예방).
            if not str(new_slug).startswith("url__item__"):
                linked_to_concept = True

        # 3) item 의 topics → wiki_pages 보장 (2026-05-27 D11, 사용자 mental model).
        #    YouTube/GitHub/arxiv 등 external_id 가 있는 URL 은 그 external_id 가
        #    곧 자기 wiki — `yt__byo7yew9-oq` 같은 slug. self_wiki (`url__item__<uuid>`)
        #    는 external_id 없는 일반 URL (블로그/뉴스 등) fallback.
        #    옛 ca431aa 의 self_wiki 무조건 INSERT 는 외부 ID wiki + self_wiki 가
        #    동시 생성되는 중복 (6,625건) 의 원인 — 제거.
        #
        #    backfill (`wiki_backfill_from_topics`) 와 sanitize_wiki_slug 공유 →
        #    같은 slug 패턴이라 신규 ingest 자료와 backfill 23k wiki 가 wiki_pages
        #    에서 자연 매칭 (ON CONFLICT find_or_create).
        topics_rows = (await session.execute(
            text("""
                SELECT t.id, t.slug, t.title, it.confidence, it.role
                FROM item_topics it
                JOIN topics t ON t.id = it.topic_id
                WHERE it.item_id = :item_id
            """),
            {"item_id": str(item_id)},
        )).mappings().all()

        # D10.6 (2026-05-28) 중복 wiki fix — 자기 정체성 topic 만 wiki 로 승격.
        # 옛 코드는 it.confidence 를 안 읽어서 cross-modal 단서 (confidence=0.7,
        # 예: YouTube 설명란의 github 링크) 까지 primary wiki 로 둔갑시켰다 → 같은
        # 자료 1개가 yt__/github__/url__item__ 여러 wiki 로 쪼개짐. 이제 confidence
        # 미달 단서는 item_topics 관계로만 남기고 wiki 는 안 만든다. 사용자가 그
        # github repo 를 *직접* ingest 하면 그때 github__ 가 진짜 primary wiki 가 됨
        # (M:N 설계 의도 유지).
        topics_to_wiki, skipped_clue_topics = _select_identity_topics_for_wiki(
            topics_rows, self.IDENTITY_TOPIC_MIN_CONFIDENCE,
        )

        # 사진(document) + URL 캡션 → 그 URL 의 위키에 figure 로 link, self_wiki 스킵
        # (2026-05-29). 텔레그램 '사진+URL' 한 메시지가 사진/URL 2 item 으로 쪼개져
        # 사진이 고아 photo 위키 되는 것 방지. caption(user_notes)의 URL → external_id
        # → 그 위키. 위키 아직 없으면(URL 미classify) self_wiki fallback — 주기
        # link_photo_captions 가 보정. (Option B: 사진은 별 item 유지, 소스로 link.)
        photo_figure_linked = False
        if item.get("source_type") == "document":
            cap_url = first_url(item.get("user_notes"))
            cap_primary = (
                primary_external_id(extract_external_ids(url=cap_url, text=None))
                if cap_url else None
            )
            if cap_primary is not None:
                fig_slug = sanitize_wiki_slug(cap_primary.slug)
                fig_row = (await session.execute(
                    text("SELECT id FROM wiki_pages WHERE slug = :slug"),
                    {"slug": fig_slug},
                )).first()
                if fig_row:
                    fig_pid = str(fig_row[0])
                    await session.execute(_UPSERT_WIKI_LINK_SQL, {
                        "page_id": fig_pid, "item_id": str(item_id),
                        "confidence": 1.0, "role": "figure",
                    })
                    if fig_pid not in linked_page_ids:
                        linked_page_ids.append(fig_pid)
                    photo_figure_linked = True

        self_wiki_created = False
        self_wiki_skipped_concept = False
        for t in topics_to_wiki:
            is_self_fallback = t["slug"].startswith("url:item:")
            # 사진을 caption URL 위키에 figure 로 연결했으면 self_wiki 안 만듦.
            if is_self_fallback and photo_figure_linked:
                continue
            # 중복 예방 (2026-06-04): 이미 개념/외부 wiki 에 연결됐으면 self_wiki 스킵.
            # 같은 논문이 'cosmos-3-...'(개념) + 'url__item__<uuid>'(self) 로 쪼개지던
            # 문제 차단. item 은 개념 wiki 에 살아있으니 self 는 군더더기 — 안 만든다.
            # (개념 매칭이 전혀 없을 때만 self_wiki 가 그 item 의 유일한 home 으로 생성.)
            if is_self_fallback and linked_to_concept:
                self_wiki_skipped_concept = True
                continue
            wiki_slug = sanitize_wiki_slug(t["slug"])
            wiki_title = t["title"] or item.get("title") or wiki_slug
            wiki_desc = (item.get("summary") or "")[:500] or None
            row = (await session.execute(_CREATE_WIKI_PAGE_SQL, {
                "slug": wiki_slug,
                "title": wiki_title,
                "description": wiki_desc,
            })).first()
            if not row:
                continue
            pid = str(row[0])
            # role: external_id wiki = 'primary' (그 자료의 주 wiki),
            #       fallback (self_wiki) = 'self' (외부 ID 없는 자료의 1:1 wiki)
            role = "self" if is_self_fallback else "primary"
            if pid not in linked_page_ids:
                await session.execute(_UPSERT_WIKI_LINK_SQL, {
                    "page_id": pid,
                    "item_id": str(item_id),
                    "confidence": 1.0,
                    "role": role,
                })
                linked_page_ids.append(pid)
            if is_self_fallback:
                self_wiki_created = True

        # 4) 매칭된 모든 wiki_pages.body_status = 'pending' (eager 합성 trigger)
        if linked_page_ids:
            await session.execute(_MARK_STALE_SQL, {"page_ids": linked_page_ids})

        output_meta = {
            "matched_count": len(matched),
            "new_pages_count": len(created_pages),
            "self_wiki_created": self_wiki_created,
            "self_wiki_skipped_concept": self_wiki_skipped_concept,
            "linked_page_ids": linked_page_ids,
            "created_pages": created_pages,
            "skipped_low_conf": skipped_low_conf,
            # D10.6 — confidence 미달로 wiki 승격 제외된 cross-modal 단서 (traceability)
            "skipped_clue_topics": [
                {
                    "slug": t["slug"],
                    "confidence": float(t["confidence"] or 0),
                    "role": t["role"],
                }
                for t in skipped_clue_topics
            ],
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

def _select_identity_topics_for_wiki(
    topics_rows: list[Any], min_confidence: float,
) -> tuple[list[Any], list[Any]]:
    """item 의 topics 중 wiki 로 승격할 '자기 정체성' topic 만 골라낸다 (D10.6).

    auto_link_topics 는 자료의 primary external_id (또는 external_id 없을 때
    fallback `url:item:<uuid>`) 를 confidence=1.0 으로, cross-modal 단서 (예:
    YouTube 설명란의 github 링크) 를 confidence=0.7 로 단다. confidence>=
    min_confidence 인 자기 정체성만 wiki 로 승격하고, 낮은 confidence 단서는
    wiki 를 안 만든다 (item_topics 관계로만 남음 → 그래프엔 보임).

    external_id topic (`yt:`, `github:`, `arxiv:` 등) 이 하나라도 있으면 그것만
    (self_wiki skip, 사용자 mental model), 없으면 fallback topic (= self_wiki) 처리.

    Returns: (topics_to_wiki, skipped_clue_topics)
      - topics_to_wiki: wiki 로 승격할 topic rows
      - skipped_clue_topics: confidence 미달로 제외된 cross-modal 단서 (traceability)
    """
    identity: list[Any] = []
    skipped: list[Any] = []
    for t in topics_rows:
        if float(t["confidence"] or 0) >= min_confidence:
            identity.append(t)
        else:
            skipped.append(t)

    ext_topics = [t for t in identity if not t["slug"].startswith("url:item:")]
    fallback_topics = [t for t in identity if t["slug"].startswith("url:item:")]
    topics_to_wiki = ext_topics if ext_topics else fallback_topics
    return topics_to_wiki, skipped


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


def _salvage_truncated_json(s: str) -> dict[str, Any] | None:
    """max_tokens 초과로 잘린 JSON 에서 완성된 요소까지만 복구.

    classifier 응답이 max_tokens 에 막히면 `matched` 배열 중간에서 끊긴다 (닫는
    `]}` 없음 → 일반 parse 불가). 여기서는 문자열/이스케이프 상태를 추적하며
    스캔해서, 배열 안에서 element 하나가 완성된 직후의 '안전한 절단점' 을 찾고
    그 시점에 열려 있던 컨테이너를 닫아 valid JSON 으로 만든다.

    예) `{"matched": [{...완성...}, {...잘림` → `{"matched": [{...완성...}]}`

    온전히 복구 못 하면 None — 호출 측이 RuntimeError 로 처리.
    """
    s = s.strip()
    # 코드 fence opener 가 있으면 그 다음부터
    m = re.search(r"```(?:json)?\s*", s)
    if m and s.startswith("```"):
        s = s[m.end():]
    start = s.find("{")
    if start == -1:
        return None
    s = s[start:]

    stack: list[str] = []          # 열린 컨테이너 스택 ('{' / '[')
    in_str = False
    esc = False
    safe_cut = -1                  # 안전하게 자를 위치 (배열 element 완성 직후)
    safe_stack: list[str] = []     # 그 시점의 컨테이너 스택 스냅샷

    for i, ch in enumerate(s):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
                # 배열 안의 문자열 element 가 완성된 지점
                if stack and stack[-1] == "[":
                    safe_cut = i + 1
                    safe_stack = list(stack)
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append(ch)
        elif ch == "}":
            if stack and stack[-1] == "{":
                stack.pop()
            # 객체 element 가 배열 안에서 완성된 지점
            if stack and stack[-1] == "[":
                safe_cut = i + 1
                safe_stack = list(stack)
        elif ch == "]":
            if stack and stack[-1] == "[":
                stack.pop()

    if safe_cut == -1:
        return None

    closing = "".join("]" if opener == "[" else "}" for opener in reversed(safe_stack))
    candidate = s[:safe_cut] + closing
    try:
        data = json.loads(candidate)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
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
