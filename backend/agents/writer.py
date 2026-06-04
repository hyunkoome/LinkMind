"""
WriterAgent — wiki page body (한국어 markdown) 합성.

설계:
  - build_context: RetrieverAgent 를 sub-agent 로 호출 → fresh WikiContext.
                   trigger_reason 도 ctx.extra 에서 추출.
  - invoke: YAML prompt (writer_v1) 로드 + WikiContext 를 XML 섹션으로 organize
            → vLLM Qwen2.5-7B chat → markdown body.
  - persist: wiki_pages.body / body_status='completed' / version_number+1
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
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.base import AgentBase, AgentContext, load_prompt
from backend.agents.retriever import RetrieverAgent
from backend.embedding.factory import get_embedding_provider
from backend.embedding.wiki_qdrant import (
    WIKI_STATUS_COMPLETED,
    ensure_wiki_collection,
    upsert_wiki_page,
)
from backend.llm.base import ChatMessage
from backend.llm.factory import get_llm_provider
from backend.utils.keywords import normalize_keywords
from backend.utils.lang import FOREIGN_THRESHOLD, count_foreign_cjk

logger = logging.getLogger("linkmind.agents.writer")


# 본문 합성 모델(Qwen2.5-7B)은 Alibaba(중국) 모델이라 source 가 중국어/일본어면
# "중국어 금지" 프롬프트 규칙을 어기고 그 언어로 써버리는 native bias 가 있다.
# 2단계 방어 (2026-05-30):
#   1) 생성 직후 외국어 감지 시 경고를 붙여 _MAX_LANG_RETRIES 회 재생성.
#   2) 그래도 외국어가 남으면 → **한국어 번역 폴백**. 실측 결과 "중국어 금지하고 새로
#      써라"(생성)는 native bias 를 못 이겼지만(재생성 3회 다 중국어), "이 텍스트를
#      한국어로 번역하라"(변환)는 명확한 작업이라 Qwen 도 제대로 한다.
_MAX_LANG_RETRIES = 1  # 최초 1회 + 재생성 1회. 그 다음은 번역 폴백이 더 확실.

_LANG_RETRY_WARNING = (
    "\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "⚠️ 경고: 직전 출력에 **중국어/일본어가 섞여 있었습니다**. 이는 절대 허용되지 않습니다.\n"
    "본문 전체를 **오직 한국어로만** 다시 작성하세요. source 자료가 중국어/영어/일본어여도\n"
    "내용을 한국어로 풀어서 다시 쓰고, 한자·가나·중국어 문장을 단 한 글자도\n"
    "포함하지 마세요. 영문 기술 용어/고유명사(LoRA, ROS2, Isaac Sim 등)만 원문 유지.\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
)

# 번역 폴백용 system prompt — 재생성으로 못 고친 외국어 본문을 한국어로 변환.
_TRANSLATE_SYSTEM = (
    "당신은 전문 번역가입니다. 입력은 위키 페이지 본문(markdown)이며 일부 문장이 "
    "중국어 또는 일본어로 되어 있습니다. 이를 한국어 본문으로 변환하세요.\n\n"
    "규칙:\n"
    "- 중국어·일본어 문장을 모두 자연스러운 한국어로 번역한다.\n"
    "- markdown 구조를 그대로 유지: ## 헤더 이름, > 인용, [N] citation 번호, 줄바꿈.\n"
    "- 영문 기술 용어·고유명사·약어(LoRA, ROS2, Isaac Sim, GitHub 등)는 번역하지 말고 원문 유지.\n"
    "- 이미 한국어인 문장은 그대로 둔다.\n"
    "- 한자·가나·중국어 문장을 단 한 글자도 남기지 않는다.\n"
    "- 설명이나 머리말 없이 변환된 본문만 출력한다."
)


_UPDATE_BODY_SQL = text("""
    UPDATE wiki_pages
    SET body = :body,
        body_model = :body_model,
        body_prompt_version = :body_prompt_version,
        body_generated_at = now(),           -- SQL now() (UTC). 옛 Python datetime.utcnow()
                                             -- 은 naive 라 timestamptz 에 세션 TZ(KST)로
                                             -- 오해석돼 9h 어긋났음 (2026-05-29 fix).
        body_status = 'completed',
        body_processing_started_at = NULL,   -- 처리 끝났으니 marker clear
        -- title = body 의 # 헤더 (정제 제목). 옛날엔 raw item title(SNS 제목 + 해시태그
        -- + "댓글 24" 등)을 복사해 지저분했음 — writer 가 # 헤더에 만든 깔끔한 제목으로
        -- 통일 (2026-05-30, 사용자 명시). 못 뽑으면(None) 기존 title 유지 (COALESCE).
        title = COALESCE(:title, title),
        -- description = body 의 TL;DR (리스트 카드 미리보기). 옛날엔 item summary 를
        -- 복사해 중국어가 섞였음 — 이제 한국어 body TL;DR 로 통일 (2026-05-30, 사용자 명시).
        -- TL;DR 못 뽑으면(None) 기존 description 유지 (COALESCE).
        description = COALESCE(:description, description)
    WHERE id = :page_id
""")


# wave-2d: body 의 ## Keywords 섹션을 파싱해 wiki_pages.keywords 에 merge.
# user_action 보존을 위해 array_cat + DISTINCT 가 이상적이지만 단순하게 덮어쓰기 →
# 사용자 추가 키워드는 별 API 로 보존 (DB 측에서 합산은 wave-3).
_UPDATE_KEYWORDS_SQL = text("""
    UPDATE wiki_pages SET keywords = :keywords WHERE id = :page_id
""")


# 2026-05-27: ON CONFLICT DO NOTHING 추가 — daemon (concurrency 4) + batch CLI
# (concurrency 4) 가 같은 page 동시 처리 시 (page_id, version_number) UNIQUE
# 충돌. 두 번째 INSERT silent skip — 어차피 같은 body 라 정합성 OK.
# _UPDATE_BODY_SQL 은 idempotent (last writer wins).
_INSERT_VERSION_SQL = text("""
    INSERT INTO wiki_page_versions (
        page_id, version_number, body, body_model, body_prompt_version,
        agent_run_id, trigger_reason
    ) VALUES (
        :page_id, :version_number, :body, :body_model, :body_prompt_version,
        :agent_run_id, :trigger_reason
    )
    ON CONFLICT (page_id, version_number) DO NOTHING
    RETURNING id
""")


# writer 시작 시 — body_processing_started_at 마킹 (시각 구분용, 2026-05-27).
# body_status 는 'pending' 그대로 (이미 pending 이거나 ready → pending). frontend 가
# started_at NOT NULL 이면 '진행 중' 으로 시각 강조.
_MARK_GENERATING_SQL = text("""
    UPDATE wiki_pages
    SET body_status = 'pending',
        body_processing_started_at = now()
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

    async def _translate_to_korean(self, provider, body: str) -> str:  # noqa: ANN001
        """외국어가 섞인 body 를 한국어로 번역 (생성 재시도가 실패했을 때 폴백).

        번역은 "입력을 그대로 한국어로" 라는 명확한 변환 작업이라 Qwen 의 중국어
        native bias 에 덜 휘둘린다. temperature=0 (결정적). 실패하면 빈 문자열 →
        caller 가 원본 유지.
        """
        try:
            resp = await provider.chat(
                messages=[
                    ChatMessage(role="system", content=_TRANSLATE_SYSTEM),
                    ChatMessage(role="user", content=body),
                ],
                model=self.llm_model,
                temperature=0.0,
                max_tokens=2048,
            )
            return resp.text.strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning("writer 번역 폴백 LLM 호출 실패 (원본 유지): %s", exc)
            return ""

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

        # body_processing_started_at = now() 마킹 (UI 'generating' 시각화).
        # ★ 즉시 commit ★ — 이 mark 와 invoke() 끝의 clear(started_at=NULL)가 같은
        # 미커밋 트랜잭션 안에 있으면, 다른 세션(frontend stats)은 set→clear 를 한
        # 번에 보게 돼 'generating'(started_at NOT NULL)을 영영 못 본다. ~15s LLM
        # 합성 동안 frontend 가 'generating'(blue pulse)을 보려면 mark 를 먼저
        # commit 해 가시화해야 한다 (2026-05-29 fix). 실패 시엔 started_at 이 남지만
        # 5분 후 'queuing' 으로 자연 복귀 + daemon backoff 재시도.
        await ctx.session.execute(
            _MARK_GENERATING_SQL,
            {"page_id": str(ctx.related_wiki_page_id)},
        )
        await ctx.session.commit()

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

        # LLM 호출 — vLLM Gemma 4 26B-A4B (context 16384, KV fp8, 2026-05-30 Qwen 에서 교체).
        # max_tokens 6144 — 풍부한 위키 위해 상향. 자료 많은 위키는 input 이 크므로(82 source
        #   = ~4100 토큰) 8192 context 로는 output 4096 과 충돌(8193>8192 에러)했다. KV fp8 로
        #   context 를 16384 로 키워 input(최대 ~10000) + output 6144 둘 다 수용.
        # temperature 0.6 — 옛 0.1 은 너무 결정적이라 반복(degenerate) 위험. Gemma 권장
        #   1.0 과 위키 사실성(낮은 temp) 사이 절충. 필요 시 샘플 보고 조정.
        provider = get_llm_provider()

        # ── 언어 안전장치 (2026-05-30) ──
        # Qwen2.5-7B 는 중국 모델이라 source 가 중국어/일본어면 본문도 그 언어로
        # 써버리는 native bias 가 있다 (프롬프트의 "중국어 금지" 규칙을 어김).
        # 생성 직후 count_foreign_cjk 로 검사해 외국어가 임계 초과로 섞이면 더 강한
        # 경고를 붙여 최대 _MAX_LANG_RETRIES 회 재생성. 끝까지 실패하면 마지막 결과를
        # 저장하되 error 로그 (빈 본문보단 나음 + 백필로 추후 재시도 가능).
        raw_body = ""
        body_model = ""
        llm_resp = None
        extra_warning = ""
        for attempt in range(1 + _MAX_LANG_RETRIES):
            llm_resp = await provider.chat(
                messages=[
                    ChatMessage(role="system", content=system_msg),
                    ChatMessage(role="user", content=user_msg + extra_warning),
                ],
                model=self.llm_model,
                temperature=0.6,
                max_tokens=6144,
            )
            raw_body = llm_resp.text.strip()
            body_model = f"{llm_resp.provider}/{llm_resp.model}"
            foreign_count = count_foreign_cjk(raw_body)
            if foreign_count <= FOREIGN_THRESHOLD:
                break
            logger.warning(
                "writer body 외국어(중국어/일본어) %d자 감지 — 재생성 %d/%d (page=%s)",
                foreign_count, attempt + 1, _MAX_LANG_RETRIES, page_id,
            )
            extra_warning = _LANG_RETRY_WARNING

        # ── 번역 폴백 — 재생성으로도 외국어가 남으면 한국어로 번역 ──
        # "새로 써라"(생성)는 native bias 를 못 이기지만 "번역하라"(변환)는 잘 한다.
        if count_foreign_cjk(raw_body) > FOREIGN_THRESHOLD:
            before = count_foreign_cjk(raw_body)
            logger.warning(
                "writer body 외국어 %d자 — 재생성 실패, 한국어 번역 폴백 (page=%s)",
                before, page_id,
            )
            translated = await self._translate_to_korean(provider, raw_body)
            after = count_foreign_cjk(translated)
            # 번역이 외국어를 줄였을 때만 채택 (드물게 번역이 더 망가지면 원본 유지)
            if translated and after < before:
                raw_body = translated
            if count_foreign_cjk(raw_body) > FOREIGN_THRESHOLD:
                logger.error(
                    "writer body 번역 후에도 외국어 %d자 잔존 — 마지막 결과 저장 (page=%s). "
                    "백필 재실행으로 추후 재시도 가능.",
                    count_foreign_cjk(raw_body), page_id,
                )

        # ── 사용자 명시 (2026-05-26): body 와 aside 의 데이터 중복 정리 ──
        # body 에서 ## Sources / ## Cross-links / ## Keywords 섹션 제거.
        # 이 3 섹션은 DB (wiki_page_items, keywords, cross-link 자동) 기반으로
        # frontend aside 가 별도 표시 — body 안 중복 X. body 는 narrative 만.
        # LLM 은 prompt 에 따라 7 섹션 다 출력 (키워드 자동 추출 필요), 우리가 cleanup.
        # 키워드 정규화 (2026-05-29) — 영문 only(CJK/한글 삭제) + 소문자-대시 + dedup.
        extracted_keywords = normalize_keywords(_parse_keywords_section(raw_body))
        body = _strip_metadata_sections(raw_body)
        # 제목(# 헤더) + TL;DR(> 인용) 추출 → wiki_pages.title / description. 한국어·정제 통일.
        clean_title = _extract_title(body)
        tldr = _extract_tldr(body)

        # 위키 본문에 figure 삽입 (사용자 명시 2026-06-04: 모델/시스템 아키텍처 + 결과
        # 그림 필수). retriever 가 모은 Docling figure(caption + file_hash, 우선순위 정렬)
        # 를 markdown 이미지로 본문 끝 ## 그림 섹션에 결정론적으로 추가 — LLM 이 URL 을
        # 지어내지 않게 코드가 직접. title/tldr 추출 뒤라 제목/요약엔 영향 없음.
        body = _append_figures(body, wiki_context.get("figures") or [])

        # version+1 결정 (latest_version 은 retriever 가 가져옴)
        latest = int(wiki_context["page"]["latest_version"] or 0)
        new_version = latest + 1

        # 1) wiki_pages.body UPDATE (body_generated_at 은 SQL now() — 위 SQL 참고)
        await session.execute(_UPDATE_BODY_SQL, {
            "body": body,
            "body_model": body_model,
            "body_prompt_version": self.agent_version,
            "page_id": str(page_id),
            "title": clean_title,
            "description": tldr,
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
                    # Postgres wiki_pages.body_status (위 _UPDATE_BODY_SQL = 'completed')
                    # 와 반드시 동일. 옛날엔 'ready' 로 잘못 넣어 ask.py 의 _retrieve_wikis
                    # (status_filter) 가 항상 0건 → 하이브리드 RAG 위키 본문이 빠졌었음
                    # (2026-06-04 fix). 단일 상수로 통일.
                    "body_status": WIKI_STATUS_COMPLETED,
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


def _extract_title(body: str) -> str | None:
    """body 의 `# 헤더`(첫 H1)를 제목으로 추출.

    writer 가 raw item title(SNS 제목·해시태그·"댓글 N" 등 지저분)을 받아도 `# 헤더`엔
    핵심 제목(예: "CLOC")을 만든다. 이를 wiki_pages.title 로 써서 리스트·상세 제목을
    정제 (옛날엔 raw item title 복사라 지저분했음). `## ` (H2)는 제외, 첫 `# ` 만.
    너무 길면(>200자) raw 가 그대로 들어온 것으로 보고 버림(None → 기존 유지).
    """
    if not body:
        return None
    for line in body.split("\n"):
        s = line.strip()
        if s.startswith("# "):
            title = s[2:].strip().strip("#").strip()
            if title and len(title) <= 200:
                return title
            return None
        if s and not s.startswith("#"):
            # 첫 비어있지 않은 줄이 H1 이 아니면 헤더 없음
            return None
    return None


def _extract_tldr(body: str) -> str | None:
    """body 의 TL;DR (markdown `> ...` 인용 블록) 을 한 줄 텍스트로 추출.

    writer_v1.yaml 형식상 `# title` 다음에 `> {TL;DR}` 가 온다. 리스트 카드의
    description 미리보기로 쓴다 (옛날엔 item summary 복사라 중국어가 섞였음).
    여러 줄 인용이면 합쳐서 반환. `>` 블록이 없으면 None (caller 가 기존 유지).
    """
    if not body:
        return None
    lines: list[str] = []
    started = False
    for line in body.split("\n"):
        s = line.strip()
        if s.startswith(">"):
            started = True
            lines.append(s.lstrip(">").strip())
        elif started:
            # 인용 블록 끝 (빈 줄 또는 다른 내용)
            break
    text = " ".join(p for p in lines if p).strip()
    # 모델이 가끔 "TL;DR:" / "요약:" 접두어를 붙임 — 미리보기엔 군더더기라 제거.
    for prefix in ("TL;DR:", "TL;DR :", "**TL;DR**:", "TL;DR", "요약:", "요약 :"):
        if text[: len(prefix)].lower() == prefix.lower():
            text = text[len(prefix):].strip()
            break
    return text or None


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


def _append_figures(body: str, figures: list[dict[str, Any]]) -> str:
    """위키 본문 끝에 '## 그림' 섹션으로 figure 삽입 (markdown 이미지 + caption).

    figures 는 retriever 가 우선순위 정렬한 Docling figure 목록 [{file_hash, caption, ...}].
    URL 은 /files/{file_hash} (첨부 서빙 엔드포인트). LLM 출력이 아니라 코드가 직접
    구성하므로 URL 이 정확 (이미지 깨짐 없음). figure 없으면 body 그대로.
    """
    if not figures:
        return body
    lines = ["", "", "## 그림", ""]
    n = 0
    for f in figures:
        fh = f.get("file_hash")
        if not fh:
            continue
        cap = (f.get("caption") or "").strip()
        alt = cap.replace("]", " ").replace("[", " ") or "figure"
        lines.append(f"![{alt}](/files/{fh})")
        if cap:
            lines.append("")
            lines.append(f"*{cap}*")
        lines.append("")
        n += 1
    if n == 0:
        return body
    return body.rstrip() + "\n" + "\n".join(lines).rstrip() + "\n"


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
    # (2026-05-30) 6→8 + summary 200→400 — Gemma 8192 context + max_tokens 4096 여유로
    # source 를 더 풍부하게 전달해 본문 깊이 향상.
    DEEP_TOP_N = 8
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
            summary_short = s["summary"][:400]
            sources_block_lines.append(f"  요약: {summary_short}")
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
