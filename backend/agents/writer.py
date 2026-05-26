"""
WriterAgent — wiki page body (한국어 markdown) 합성.

설계:
  - build_context: RetrieverAgent 를 sub-agent 로 호출 → fresh WikiContext.
                   trigger_reason 도 ctx.extra 에서 추출.
  - invoke: YAML prompt (writer_v1) 로드 + WikiContext 를 XML 섹션으로 organize
            → vLLM Qwen2.5-7B chat → markdown body.
  - persist: wiki_pages.body / body_status='ready' / version_number+1
             + wiki_page_versions INSERT (이전 버전 보존, Phase 4 학습 신호).

state-centric — agent 내부에 conversation history X. 매 호출이 fresh.

trigger_reason 종류 (ctx.extra['trigger_reason']):
  - 'first_gen'           — wiki_pages 생성 후 첫 합성
  - 'stale_regenerate'    — 새 item link 후 재합성 (wave-1 의 단순 패턴)
  - 'incremental_add'     — 옛 body 에 신규 자료만 patch (wave-2)
  - 'user_request'        — 사용자가 [재합성] 버튼
  - 'lint_fix'            — critic 결과 (wave-3)
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.base import AgentBase, AgentContext, load_prompt
from backend.agents.retriever import RetrieverAgent
from backend.embedding.factory import get_embedding_provider
from backend.embedding.wiki_qdrant import ensure_wiki_collection, upsert_wiki_page
from backend.llm.base import ChatMessage
from backend.llm.factory import get_llm_provider

logger = logging.getLogger("linkmind.agents.writer")


_UPDATE_BODY_SQL = text("""
    UPDATE wiki_pages
    SET body = :body,
        body_model = :body_model,
        body_prompt_version = :body_prompt_version,
        body_generated_at = :body_generated_at,
        body_status = 'ready'
    WHERE id = :page_id
""")


# wave-2d: body 의 ## Keywords 섹션을 파싱해 wiki_pages.keywords 에 merge.
# user_action 보존을 위해 array_cat + DISTINCT 가 이상적이지만 단순하게 덮어쓰기 →
# 사용자 추가 키워드는 별 API 로 보존 (DB 측에서 합산은 wave-3).
_UPDATE_KEYWORDS_SQL = text("""
    UPDATE wiki_pages SET keywords = :keywords WHERE id = :page_id
""")


_INSERT_VERSION_SQL = text("""
    INSERT INTO wiki_page_versions (
        page_id, version_number, body, body_model, body_prompt_version,
        agent_run_id, trigger_reason
    ) VALUES (
        :page_id, :version_number, :body, :body_model, :body_prompt_version,
        :agent_run_id, :trigger_reason
    )
    RETURNING id
""")


_MARK_GENERATING_SQL = text("""
    UPDATE wiki_pages SET body_status = 'generating'
    WHERE id = :page_id
""")


class WriterAgent(AgentBase):
    """wiki page body 합성 + 영속화."""

    agent_name = "writer"
    agent_version = "v1"
    # vLLM 의 effective default 사용 — settings 의 default_llm_provider/model 따라.
    # 명시 override 가능 (e.g. 'vllm/Qwen/Qwen2.5-7B-Instruct')
    llm_model: str | None = None

    # YAML prompt 캐시 (process-local)
    _prompt: dict[str, Any] | None = None

    def _load_prompt(self) -> dict[str, Any]:
        if self._prompt is None:
            self._prompt = load_prompt("writer", self.agent_version)
        return self._prompt

    async def build_context(self, ctx: AgentContext) -> dict[str, Any]:
        if not ctx.related_wiki_page_id:
            raise ValueError("WriterAgent: related_wiki_page_id 필수")

        # state-centric: retriever sub-agent 호출 → fresh WikiContext
        retriever = RetrieverAgent()
        retr_result = await retriever.run(ctx)
        if not retr_result.ok or retr_result.output_meta is None:
            raise RuntimeError(
                f"retriever failed: {retr_result.error or 'no output_meta'}"
            )

        wiki_context = retr_result.output_meta
        trigger_reason = ctx.extra.get("trigger_reason", "user_request")

        # body_status = 'generating' 로 마킹 (다른 동시 합성 방지 + UI 가시화)
        await ctx.session.execute(
            _MARK_GENERATING_SQL,
            {"page_id": str(ctx.related_wiki_page_id)},
        )

        return {
            "wiki_page_id": str(ctx.related_wiki_page_id),
            "wiki_context": wiki_context,
            "trigger_reason": trigger_reason,
            "retriever_run_id": str(retr_result.agent_run_id) if retr_result.agent_run_id else None,
        }

    async def invoke(self, input_payload: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
        assert self._ctx is not None
        session: AsyncSession = self._ctx.session

        page_id = UUID(input_payload["wiki_page_id"])
        wiki_context = input_payload["wiki_context"]
        trigger_reason = input_payload["trigger_reason"]

        prompt = self._load_prompt()
        system_msg = prompt["system"]
        user_msg = _build_user_message(prompt["user_template"], wiki_context)

        # LLM 호출 — vLLM Qwen2.5-7B (context window 8192 토큰).
        # temperature 0.1 — 구성 일관성 우선. max_tokens 1024 — wiki body 700-1500자
        # 면 ~500-900 token. 2048 은 가끔 "list 무한 반복" 으로 가득 채워 잘림.
        # 1024 로 줄이면 자연 EOS 의존 + 잘리는 케이스 자른다.
        provider = get_llm_provider()
        llm_resp = await provider.chat(
            messages=[
                ChatMessage(role="system", content=system_msg),
                ChatMessage(role="user", content=user_msg),
            ],
            model=self.llm_model,
            temperature=0.1,
            max_tokens=1024,
        )
        raw_body = llm_resp.text.strip()
        body_model = f"{llm_resp.provider}/{llm_resp.model}"

        # ── 사용자 명시 (2026-05-26): body 와 aside 의 데이터 중복 정리 ──
        # body 에서 ## Sources / ## Cross-links / ## Keywords 섹션 제거.
        # 이 3 섹션은 DB (wiki_page_items, keywords, cross-link 자동) 기반으로
        # frontend aside 가 별도 표시 — body 안 중복 X. body 는 narrative 만.
        # LLM 은 prompt 에 따라 7 섹션 다 출력 (키워드 자동 추출 필요), 우리가 cleanup.
        extracted_keywords = _parse_keywords_section(raw_body)
        body = _strip_metadata_sections(raw_body)

        # version+1 결정 (latest_version 은 retriever 가 가져옴)
        latest = int(wiki_context["page"]["latest_version"] or 0)
        new_version = latest + 1
        generated_at = datetime.utcnow()

        # 1) wiki_pages.body UPDATE
        await session.execute(_UPDATE_BODY_SQL, {
            "body": body,
            "body_model": body_model,
            "body_prompt_version": self.agent_version,
            "body_generated_at": generated_at,
            "page_id": str(page_id),
        })

        # 1.5) extracted_keywords 는 위에서 raw_body 로부터 파싱한 결과를 그대로 사용.
        #      사용자가 frontend 에서 추가한 키워드는 별 API 로 별도 보존 — wave-3 에
        #      병합 로직 (array_cat + DISTINCT) 정교화. 지금은 LLM 결과로 덮어쓰기.
        await session.execute(_UPDATE_KEYWORDS_SQL, {
            "keywords": extracted_keywords,
            "page_id": str(page_id),
        })

        # 2) wiki_page_versions INSERT (이전 버전 보존 = Phase 4 학습 신호)
        await session.execute(_INSERT_VERSION_SQL, {
            "page_id": str(page_id),
            "version_number": new_version,
            "body": body,
            "body_model": body_model,
            "body_prompt_version": self.agent_version,
            "agent_run_id": None,    # 이 agent_run 의 id 가 적립 직전이라 NULL — wave-2 에 hook 으로 보강
            "trigger_reason": trigger_reason,
        })

        # 3) Qdrant wiki_pages 컬렉션에 body embedding upsert (wave-1f).
        #    실패해도 agent 결과에 영향 X — wiki 검색 인덱스만 stale.
        qdrant_upsert_ok = False
        try:
            embedder = get_embedding_provider()
            await ensure_wiki_collection(dim=embedder.dim)
            emb_result = await embedder.embed([body])
            await upsert_wiki_page(
                page_id=str(page_id),
                vector=emb_result.vectors[0],
                payload={
                    "slug": wiki_context["page"]["slug"],
                    "title": wiki_context["page"]["title"],
                    "description": wiki_context["page"].get("description"),
                    "source_count": len(wiki_context["sources"]),
                    "body_status": "ready",
                    "is_pinned": bool(wiki_context["page"].get("is_pinned", False)),
                    "version_number": new_version,
                },
            )
            qdrant_upsert_ok = True
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Qdrant wiki_pages upsert 실패 (page=%s, 계속): %s",
                page_id, exc,
            )

        # commit 은 caller (API 의 session_factory outer commit) 책임 — agent 는 mutate 만
        output_meta = {
            "body_length": len(body),
            "body_model": body_model,
            "version_number": new_version,
            "trigger_reason": trigger_reason,
            "source_count": len(wiki_context["sources"]),
            "attachment_count": wiki_context["attachment_count"],
            "cross_link_count": len(wiki_context["cross_link_candidates"]),
            "retriever_run_id": input_payload.get("retriever_run_id"),
            "llm_usage": llm_resp.usage,
            "qdrant_upsert_ok": qdrant_upsert_ok,
            "keywords_count": len(extracted_keywords),
            "keywords_sample": extracted_keywords[:10],
        }
        return body, output_meta


_METADATA_HEADER_PATTERNS = (
    "## Sources",
    "## sources",
    "## Cross-links",
    "## cross-links",
    "## Cross Links",
    "## Keywords",
    "## keywords",
    "## 키워드",
    "## 소스",
    "## 관련 페이지",
    "## 참고 자료",
    "## User notes",
    "## 사용자 메모",
    "## 최근 추가",
    "## Latest",
)


def _strip_metadata_sections(body: str) -> str:
    """body 에서 metadata 섹션 (## Sources / ## Cross-links / ## Keywords + 변형) 제거.

    이 섹션들은 frontend aside (KeywordsEditor + Sources 패널 + Cross-links 패널)
    가 DB 기반으로 별도 표시 — body 안 중복 방지 (사용자 명시 2026-05-26).

    가장 먼저 등장하는 metadata 헤더 위치부터 body 끝까지 잘라냄.
    """
    if not body:
        return body
    first_idx = -1
    for pat in _METADATA_HEADER_PATTERNS:
        # 줄 시작 기준 (앞에 \n 있어야 — 본문 안 inline 매칭 회피)
        needle = f"\n{pat}"
        idx = body.find(needle)
        if idx == -1:
            continue
        if first_idx == -1 or idx < first_idx:
            first_idx = idx
    if first_idx == -1:
        return body.rstrip()
    return body[:first_idx].rstrip()


def _parse_keywords_section(body: str) -> list[str]:
    """body 에서 '## Keywords' 섹션의 키워드 list 추출.

    형식: '## Keywords' 다음 줄 들에 쉼표 구분 (또는 여러 줄). LLM 이 가끔 다른
    형식 (bullet list 등) 으로도 출력하므로 너그럽게 파싱.
    """
    if not body:
        return []
    lines = body.split("\n")
    in_keywords = False
    collected: list[str] = []
    for line in lines:
        stripped = line.strip()
        # 다음 ## 헤더 만나면 종료
        if stripped.startswith("## "):
            if in_keywords:
                break
            if "keyword" in stripped.lower():
                in_keywords = True
            continue
        if not in_keywords:
            continue
        if not stripped:
            continue
        # bullet list 도 처리 ('- keyword' or '* keyword')
        if stripped.startswith(("- ", "* ")):
            stripped = stripped[2:].strip()
        # 쉼표 구분 또는 한 줄에 한 키워드
        for kw in stripped.split(","):
            kw = kw.strip().strip("`").strip("#").strip()
            if kw and len(kw) <= 80:
                collected.append(kw)
    # dedup (순서 유지)
    seen: set[str] = set()
    out: list[str] = []
    for kw in collected:
        kl = kw.lower()
        if kl in seen:
            continue
        seen.add(kl)
        out.append(kw)
    return out


# ============================================================================
# user_template 의 변수 치환 (단순 f-string 패턴 — Jinja2 불필요)
# ============================================================================

def _build_user_message(template: str, wiki_context: dict[str, Any]) -> str:
    """writer_v1.yaml 의 user_template 에 wiki_context 변수 채워넣음.

    template 안의 placeholder:
      {slug} {title} {description}
      {source_count} {sources_block} {user_notes_block} {cross_link_candidates}

    [context 절약 규칙 — 2026-05-26]
      - sources top-N 만 본문 전달 (default 6). 그 이상은 'Sources' 섹션 list 만.
      - 각 source 의 summary 200자 cap (이전 400 → 200, 너무 길면 prompt 부담).
      - 첨부는 한 source 당 2개만.
      - 이렇게 해야 max_tokens=2048 안에 6 섹션 다 출력 가능.
    """
    page = wiki_context["page"]
    all_sources = wiki_context["sources"]

    # confidence DESC 정렬 (retriever 가 이미 했지만 확실히)
    sources = sorted(
        all_sources,
        key=lambda s: (s.get("confidence") or 0.0),
        reverse=True,
    )

    # 본문에 깊이 인용할 top-N + 나머지는 listing only
    DEEP_TOP_N = 6
    deep_sources = sources[:DEEP_TOP_N]
    listing_only_sources = sources[DEEP_TOP_N:]

    sources_block_lines: list[str] = []
    for i, s in enumerate(deep_sources, start=1):
        url = s.get("source_url") or "(no url)"
        title = s.get("title") or s["item_id"][:8]
        sources_block_lines.append(
            f"[{i}] {title} ({s['source_type']}) — {url}"
            f" [confidence={s.get('confidence', 1.0):.2f}, role={s.get('role') or '-'}]"
        )
        if s.get("summary"):
            summary_short = s["summary"][:200]
            sources_block_lines.append(f"  요약: {summary_short}")
        if s.get("tags"):
            sources_block_lines.append(f"  tags: {' '.join('#' + t for t in s['tags'][:6])}")
        if s.get("attachments"):
            att_summary = ", ".join(
                f"{a.get('role') or 'file'}({a.get('mime_type') or '?'})"
                for a in s["attachments"][:2]
            )
            sources_block_lines.append(f"  attachments: {att_summary}")

    # 추가 sources (listing only — LLM 이 ##Sources 섹션에 짧게 list 만)
    if listing_only_sources:
        sources_block_lines.append(
            f"\n[추가 sources, listing only — 본문 깊이 인용 X, ##Sources 섹션에 짧게 list 만]"
        )
        for j, s in enumerate(listing_only_sources, start=DEEP_TOP_N + 1):
            title = s.get("title") or s["item_id"][:8]
            sources_block_lines.append(
                f"[{j}] {title} ({s['source_type']}) [confidence={s.get('confidence', 1.0):.2f}]"
            )

    sources_block = "\n".join(sources_block_lines) if sources_block_lines else "(no sources)"

    user_notes = wiki_context.get("user_notes_combined") or "(no user notes)"
    if len(user_notes) > 500:
        user_notes = user_notes[:500] + "... (생략)"

    # cross-link 후보 top-10 까지
    cross_links_lines = [
        f"- [[{c['slug']}]] {c['title']} (shared_items={c['shared_items']})"
        for c in wiki_context.get("cross_link_candidates", [])[:10]
    ]
    cross_link_candidates = "\n".join(cross_links_lines) if cross_links_lines else "(no candidates)"

    return template.format(
        slug=page["slug"],
        title=page["title"],
        description=page.get("description") or "(no description)",
        source_count=len(sources),
        sources_block=sources_block,
        user_notes_block=user_notes,
        cross_link_candidates=cross_link_candidates,
    )
