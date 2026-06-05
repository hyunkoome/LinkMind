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
import re
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


# ──────────────────────────────────────────────────────────────────────
# 토큰 예산 — vLLM Gemma 4 26B-A4B context 16384 안에서 큰 논문도 안정 합성
# ──────────────────────────────────────────────────────────────────────
# 큰 논문 raw(예: 43k자 → ~9k+ 토큰)를 단일 호출에 통째로 넣으면 input+output 이
# 16384 를 넘어 BadRequestError(400)로 합성이 *영구 실패*(pending stuck)한다.
# 해결: raw 가 단일 호출 예산을 넘으면 **섹션별로 나눠 압축(map)** 한 뒤 그 압축
# 노트로 위키를 합성(reduce). 원본을 잘라 버리지 않으므로 뒷부분 손실 없음.
# (raw-first §2 — 발췌 truncate 는 정보 손실, map 압축은 전 구간 반영.)
_MODEL_CONTEXT_TOKENS = 16384      # vLLM max_model_len (app_settings 와 일치 유지)
_PAPER_OUTPUT_TOKENS = 7168        # 논문 reduce 출력 (충실도 — 사용자 요구)
_GENERAL_OUTPUT_TOKENS = 6144      # 개념형 출력
_PROMPT_SAFETY_MARGIN = 1024       # 라벨/figures/sources/추정오차 흡수
# 보수적 char→token 비율. markdown 표·수식·특수문자는 토큰 효율이 낮아(토큰이 많아)
# 영어 평균(~4)보다 작게 잡아 *토큰을 과대추정* → 항상 안전쪽으로 자른다. 실측
# (MCGS-SLAM: ~20000자 발췌가 ~7000토큰 ≈ 2.85)보다 더 보수적인 2.5.
_CHARS_PER_TOKEN = 2.5
# map 단계 — 한 청크에 넣을 raw 입력 토큰 예산 (출력 압축 노트 + 여유 포함해 16384 안).
_MAP_INPUT_TOKENS = 8000
_MAP_OUTPUT_TOKENS = 3072


def _estimate_tokens(textval: str) -> int:
    """char 기반 보수적 토큰 추정 (토크나이저 미로딩). 안전쪽(과대)으로 추정."""
    if not textval:
        return 0
    return int(len(textval) / _CHARS_PER_TOKEN) + 1


_HEADER_RE = re.compile(r"^#{1,6}\s+\S")


def _split_markdown_sections(raw: str) -> list[str]:
    """markdown 본문을 헤더(#~######) 경계로 섹션 분할. 헤더 앞 prefix(제목/초록)도
    한 섹션. 헤더가 거의 없는 문서면 통째 1섹션 → 청크 패킹이 길이로 다시 쪼갠다."""
    if not raw:
        return []
    sections: list[str] = []
    current: list[str] = []
    for line in raw.split("\n"):
        if _HEADER_RE.match(line) and current:
            sections.append("\n".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append("\n".join(current))
    return [s for s in sections if s.strip()]


def _pack_sections_into_chunks(sections: list[str], budget_chars: int) -> list[str]:
    """섹션들을 순서 보존하며 budget_chars 까지 묶어 청크 리스트로. 단일 섹션이
    예산을 넘으면 char 단위로 강제 분할(헤더 없는 거대 본문 방어). 항상 각 청크
    len <= budget_chars 보장."""
    budget_chars = max(1, budget_chars)
    chunks: list[str] = []
    cur: list[str] = []
    cur_len = 0
    for sec in sections:
        if len(sec) > budget_chars:
            if cur:
                chunks.append("\n\n".join(cur))
                cur, cur_len = [], 0
            for i in range(0, len(sec), budget_chars):
                chunks.append(sec[i : i + budget_chars])
            continue
        if cur and cur_len + len(sec) + 2 > budget_chars:
            chunks.append("\n\n".join(cur))
            cur, cur_len = [], 0
        cur.append(sec)
        cur_len += len(sec) + 2
    if cur:
        chunks.append("\n\n".join(cur))
    return chunks


# 논문 raw 본문 — writer 가 map-reduce 시 primary item 의 raw 를 직접 fetch.
# (retriever 는 output_meta JSONB 비대 방지로 짧은 excerpt 만 준다 — map 입력은
#  전 구간이 필요하므로 writer 가 그때 읽어 처리 후 버린다.)
_FETCH_ITEM_RAW_SQL = text("SELECT raw_content FROM items WHERE id = :item_id")


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

    # YAML prompt 캐시 (process-local) — 이름별 (writer / writer_paper).
    _prompt_cache: dict[str, dict[str, Any]] = {}

    def _load_prompt(self, name: str = "writer") -> dict[str, Any]:
        cached = self._prompt_cache.get(name)
        if cached is None:
            cached = load_prompt(name, self.agent_version)
            self._prompt_cache[name] = cached
        return cached

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

    async def _map_compress_once(
        self, provider, raw_text: str, chunk_budget_chars: int,  # noqa: ANN001
    ) -> tuple[str, int]:
        """raw_text 를 섹션 청크로 나눠 각 청크를 LLM 으로 한국어 압축 노트화(map).
        반환 (결합 노트, 청크 수). 청크가 1개뿐이면(더 못 쪼갬) 원문 그대로 반환."""
        sections = _split_markdown_sections(raw_text) or [raw_text]
        chunks = _pack_sections_into_chunks(sections, chunk_budget_chars)
        if len(chunks) <= 1:
            return raw_text, 1
        map_prompt = self._load_prompt("writer_paper_map")
        notes: list[str] = []
        for idx, chunk in enumerate(chunks, start=1):
            user = map_prompt["user_template"].format(
                part_index=idx, part_total=len(chunks), chunk=chunk,
            )
            try:
                resp = await provider.chat(
                    messages=[
                        ChatMessage(role="system", content=map_prompt["system"]),
                        ChatMessage(role="user", content=user),
                    ],
                    model=self.llm_model,
                    temperature=0.3,
                    max_tokens=_MAP_OUTPUT_TOKENS,
                )
                note = resp.text.strip()
                if note:
                    notes.append(note)
            except Exception as exc:  # noqa: BLE001 — 한 청크 실패해도 나머지로 진행
                logger.warning("paper map 청크 %d/%d 압축 실패 (건너뜀): %s",
                               idx, len(chunks), exc)
        combined = "\n\n".join(notes).strip()
        return (combined or raw_text), len(chunks)

    async def _prepare_paper_body(
        self, provider, session: AsyncSession,  # noqa: ANN001
        wiki_context: dict[str, Any], system_msg: str, output_tokens: int,
    ) -> str:
        """논문 reduce(최종 위키 합성)에 넣을 paper_body 를 만든다.

        primary 논문 raw 전체를 읽어, vLLM context(16384) 안에 들어오면 통째로(충실),
        넘으면 **섹션별 map 압축**으로 전 구간을 반영한 노트로 줄인다(뒷부분 손실 없음).
        map 으로도 한 번에 안 줄면 노트를 재압축(recursive reduce, 상한 3회)."""
        pr = wiki_context.get("primary_raw") or {}
        excerpt = pr.get("excerpt") or ""
        item_id = pr.get("item_id")
        slug = (wiki_context.get("page") or {}).get("slug", "?")

        raw_full = ""
        if item_id:
            row = (await session.execute(
                _FETCH_ITEM_RAW_SQL, {"item_id": str(item_id)},
            )).first()
            raw_full = ((row[0] or "").strip()) if row else ""
        if not raw_full:
            return excerpt

        # 단일 호출 입력 예산 (output + system + figures/sources margin 제외)
        single_budget = (
            _MODEL_CONTEXT_TOKENS - output_tokens - _PROMPT_SAFETY_MARGIN
            - _estimate_tokens(system_msg)
        )
        if _estimate_tokens(raw_full) <= single_budget:
            return raw_full   # raw 가 예산 안 — excerpt 컷보다 충실하게 통째 사용

        chunk_budget_chars = int(_MAP_INPUT_TOKENS * _CHARS_PER_TOKEN)
        body = raw_full
        for depth in range(3):
            if _estimate_tokens(body) <= single_budget:
                break
            compressed, n_chunks = await self._map_compress_once(
                provider, body, chunk_budget_chars,
            )
            if n_chunks <= 1 or compressed == body:
                break   # 더 못 줄임 — 최종 _enforce_input_budget 가 truncate
            body = compressed
            logger.info(
                "paper map 압축 depth=%d: %d청크 → %d자 (raw %d자, page=%s)",
                depth + 1, n_chunks, len(body), len(raw_full), slug,
            )
        return body

    async def _koreanize_figure_captions(
        self, provider, figures: list[dict[str, Any]],  # noqa: ANN001
    ) -> list[dict[str, Any]]:
        """figure caption(영어 Docling 원본)을 한글 1문장 요약으로 교체 (element C/D).

        - 번호(Figure N / Fig. N)는 유지, 내용만 한국어로 압축.
        - LLM 1회 호출로 전 figure 일괄 처리 (figure 당 호출 X — 비용/지연 절감).
        - 실패하거나 줄 수가 안 맞으면 **원본 caption 을 짧게 절단**해 폴백(영어라도
          도배는 막음). figure 가 없으면 그대로 반환.
        """
        if not figures:
            return figures

        # 입력 — 번호 매겨 한 줄씩. caption 이 없는 figure 도 자리 유지(번호 정합).
        numbered: list[str] = []
        for i, f in enumerate(figures, start=1):
            cap = (f.get("caption") or "").strip().replace("\n", " ")
            numbered.append(f"{i}. {cap[:600] or '(no caption)'}")
        user_block = "\n".join(numbered)

        system = (
            "당신은 논문 그림 캡션을 한국어로 요약하는 도우미입니다. 입력은 번호 매겨진 "
            "figure 캡션 목록(주로 영어)입니다. 각 줄을 한국어 한 문장으로 짧게 요약하세요.\n"
            "규칙:\n"
            "- 출력은 입력과 같은 번호·같은 줄 수. '1. ...' '2. ...' 형식.\n"
            "- 'Figure N' / 'Fig. N' 같은 그림 번호가 있으면 한국어 문장 앞에 유지 "
            "(예: '그림 2. 제안 아키텍처 개요').\n"
            "- 영문 모델명·약어·고유명사(LoRA, BEV, IoU 등)는 원문 유지.\n"
            "- 30-80자 내외로 짧게. 캡션의 핵심만.\n"
            "- 중국어·일본어 금지. 설명/머리말 없이 번호 목록만 출력."
        )
        try:
            resp = await provider.chat(
                messages=[
                    ChatMessage(role="system", content=system),
                    ChatMessage(role="user", content=user_block),
                ],
                model=self.llm_model,
                temperature=0.2,
                max_tokens=1024,
            )
            parsed = _parse_numbered_captions(resp.text, len(figures))
        except Exception as exc:  # noqa: BLE001
            logger.warning("figure caption 한글 요약 LLM 실패 (원본 절단 폴백): %s", exc)
            parsed = None

        out: list[dict[str, Any]] = []
        for i, f in enumerate(figures):
            new_f = dict(f)
            kr = parsed[i] if parsed and i < len(parsed) and parsed[i] else None
            if kr:
                new_f["caption"] = kr
            else:
                # 폴백 — 원본 caption 을 짧게 절단(element C: 도배 방지)
                cap = (f.get("caption") or "").strip()
                new_f["caption"] = (cap[:120] + "…") if len(cap) > 120 else cap
            out.append(new_f)
        return out

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

        provider = get_llm_provider()

        # ── figure 한글 캡션 (element C/D) — 본문 생성 *전에* 만든다 ──
        # 논문 prompt 에 [FIGN] 라벨 + 한글 캡션을 미리 넣어 LLM 이 본문 맥락 속 적절한
        # 위치에 그림을 배치(placeholder)하게 하기 위함. file_hash 는 코드가 보관했다가
        # 생성 후 결정론적으로 치환 (LLM 이 URL 지어내지 않게). i 번째 figure = [FIG{i+1}].
        figures_kr = await self._koreanize_figure_captions(
            provider, wiki_context.get("figures") or [],
        )

        # ── 문서 타입 분기 (재설계 element A) ──
        # primary source 가 논문류면(retriever 가 doc_type='paper' + primary_raw 세팅)
        # 논문 전용 prompt + raw 본문 발췌 기반 합성. 그 외는 기존 개념형 prompt.
        doc_type = wiki_context.get("doc_type", "general")
        use_paper = doc_type == "paper" and bool(wiki_context.get("primary_raw"))
        if use_paper:
            prompt = self._load_prompt("writer_paper")
            system_msg = prompt["system"]
            max_tokens = _PAPER_OUTPUT_TOKENS
            # ── 큰 논문 안정화 (2026-06-05) ──
            # raw 가 단일 호출 예산을 넘으면 섹션별로 나눠 압축(map)해 reduce 입력을
            # 항상 context(16384) 안으로 들인다. 작은 논문은 raw 통째 사용(빠름).
            paper_body = await self._prepare_paper_body(
                provider, session, wiki_context, system_msg, max_tokens,
            )
            user_msg = _build_paper_user_message(
                prompt["user_template"], wiki_context, figures_kr,
                paper_body_override=paper_body,
            )
            # 최종 안전 가드 — figures/sources 까지 합쳐도 예산을 넘으면 body truncate.
            user_msg = _enforce_input_budget(
                prompt["user_template"], wiki_context, figures_kr,
                system_msg, user_msg, paper_body, max_tokens,
            )
            prompt_version_label = f"paper-{self.agent_version}"
        else:
            prompt = self._load_prompt("writer")
            system_msg = prompt["system"]
            max_tokens = _GENERAL_OUTPUT_TOKENS
            user_msg = _build_user_message(prompt["user_template"], wiki_context)
            prompt_version_label = self.agent_version

        # LLM 호출 — vLLM Gemma 4 26B-A4B (context 16384, KV fp8, 2026-05-30 Qwen 에서 교체).
        # max_tokens: 논문은 7168(원문 없이 이해될 만큼 충실하게 — 사용자 요구), 일반은 6144.
        #   큰 논문은 위 _prepare_paper_body 가 raw 를 map 압축해 input 을 예산 안으로 유지.
        # temperature 0.6 — 옛 0.1 은 너무 결정적이라 반복(degenerate) 위험. Gemma 권장
        #   1.0 과 위키 사실성(낮은 temp) 사이 절충. 필요 시 샘플 보고 조정.

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
                max_tokens=max_tokens,
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

        # ── 논문 제목 override (재설계 element F) ──
        # 위키 title 이 classifier slug 기반("arxiv:2603.27344")으로 들어가는 문제를
        # 코드가 결정론적으로 교정 — primary 논문 item 의 title(정확한 원제)을 우선.
        # LLM 이 # 헤더에 원제를 못 쓰는 경우(번역/축약)에도 DB title 은 원제로 통일.
        if use_paper:
            pr = wiki_context.get("primary_raw") or {}
            paper_title = (pr.get("title") or "").strip()
            if paper_title:
                clean_title = paper_title
        else:
            # de-clone (2026-06-04): 일반 self-wiki 는 **정체성 item 의 제목**으로 통일.
            # cross-link 된 논문이 섞여 LLM 이 그 논문 제목을 # 헤더로 쓰던 납치 방지
            # (예: NVIDIA 블로그 self-wiki 가 cosmos 논문 제목을 달던 문제). 개념 wiki 는
            # identity_item=None 이라 기존 LLM # 헤더 제목 유지.
            ident = wiki_context.get("identity_item") or {}
            ident_title = (ident.get("title") or "").strip()
            if ident_title and len(ident_title) <= 200:
                clean_title = ident_title

        # 위키 본문에 figure 삽입 — file_hash 로 /files/{hash} URL 을 코드가 결정론적으로
        # 구성(LLM 이 URL 지어내지 않게). figures_kr 은 위에서 한글 캡션화(element C/D) 완료.
        #   - 논문(use_paper): LLM 이 본문 맥락 속에 박은 [FIGN] placeholder 를 실제 이미지로
        #     치환(element G — 끝에 몰아넣지 않고 관련 섹션 안에). 안 쓰인 그림은 끝에 보충.
        #   - 일반: 기존대로 본문 끝 ## 그림 섹션에 일괄 추가.
        if use_paper:
            body = _insert_inline_figures(body, figures_kr)
        else:
            body = _append_figures(body, figures_kr)

        # version+1 결정 (latest_version 은 retriever 가 가져옴)
        latest = int(wiki_context["page"]["latest_version"] or 0)
        new_version = latest + 1

        # 1) wiki_pages.body UPDATE (body_generated_at 은 SQL now() — 위 SQL 참고)
        await session.execute(_UPDATE_BODY_SQL, {
            "body": body,
            "body_model": body_model,
            "body_prompt_version": prompt_version_label,
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
            "body_prompt_version": prompt_version_label,
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


def _short_alt(caption: str) -> str:
    """이미지 alt 용 짧은 라벨 — 캡션 전체가 아니라 앞 토큰만 (예: '그림 5', 'Figure 1').

    캡션 전체를 alt 로 쓰면 이미지가 깨졌을 때 alt 텍스트(=전체 캡션)가 노출돼 바로 아래
    `*캡션*` 줄과 합쳐 **캡션이 2번** 보였다(사용자 보고 2026-06-04). 짧은 라벨만 alt 로
    두면 정상 로드 시 보이지 않고, 깨져도 '그림 5' 정도만 떠 중복이 안 생긴다.
    """
    c = (caption or "").strip()
    if not c:
        return "그림"
    head = re.split(r"[.:]", c, maxsplit=1)[0].strip()   # "그림 5. ..." → "그림 5"
    head = head.replace("[", " ").replace("]", " ").strip()
    return head[:24] or "그림"


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
        # alt 는 짧은 라벨만 (전체 캡션 X — 깨진 이미지에서 캡션 2번 노출 방지)
        lines.append(f"![{_short_alt(cap)}](/files/{fh})")
        if cap:
            lines.append("")
            lines.append(f"*{cap}*")
        lines.append("")
        n += 1
    if n == 0:
        return body
    return body.rstrip() + "\n" + "\n".join(lines).rstrip() + "\n"


def _figure_markdown(f: dict[str, Any]) -> str | None:
    """figure dict → markdown 이미지 + 캡션 블록. file_hash 없으면 None."""
    fh = f.get("file_hash")
    if not fh:
        return None
    cap = (f.get("caption") or "").strip()
    out = f"![{_short_alt(cap)}](/files/{fh})"   # alt 짧게 (캡션 2번 노출 방지)
    if cap:
        out += f"\n\n*{cap}*"
    return out


# [FIG2] 표준 + [FIG4a-c] / [FIG4h, 4i] / [FIG 5] 같은 LLM 변형도 매칭(숫자 1개 캡처 +
# 뒤따르는 알파벳/콤마/공백/하이픈 흡수). 치환 못 한 placeholder 가 평문으로 남지 않게.
_FIG_LABEL_RE = re.compile(r"\[FIG\s*(\d+)[a-zA-Z0-9,\s\-]*\]")

# LLM 이 본문에 따로 쓴 '그림 N. ...' / 'Figure N: ...' 캡션형 **단독 줄**. 코드가 [FIGN]
# 치환 시 *그림 N. 캡션* 을 자동 삽입하므로 이런 평문 캡션은 중복(2번 노출)된다.
# 문장 중간 참조('그림 3에서 보듯 ~')는 숫자 뒤 [.:] 구분자가 없어 매칭 안 됨 → 보존.
# 우리가 삽입하는 캡션은 '*' 로 시작하므로(^\s*그림 에 안 걸림) 영향 없음.
_FIG_CAPTION_LINE_RE = re.compile(
    r"^\s*(?:그림|사진|Figure|Fig\.?)\s*\d+\s*[.:]",
)


def _strip_llm_figure_captions(body: str) -> str:
    """LLM 이 본문 서술로 쓴 'figure 캡션 평문 줄'을 제거 (figure 캡션 2번 노출 방지).

    코드(_figure_markdown)가 [FIGN] 치환 시 한글 캡션을 *이탤릭* 으로 삽입하는데, LLM
    이 같은 캡션을 평문으로 한 번 더 쓰면 중복된다. 캡션형 단독 줄만 골라 제거한다."""
    kept = [
        line for line in body.split("\n")
        if not _FIG_CAPTION_LINE_RE.match(line)
    ]
    return "\n".join(kept)


def _insert_inline_figures(body: str, figures_kr: list[dict[str, Any]]) -> str:
    """LLM 이 본문 맥락 속에 박은 [FIGN] placeholder 를 실제 이미지로 치환 (element G).

    - [FIG1] = figures_kr[0] … (1-based). 라벨이 단독 줄이든 문장 끝이든 그 자리를
      markdown 이미지+캡션으로 교체 — 그림이 관련 섹션 안에 배치된다(끝 몰아넣기 폐지).
    - file_hash 는 코드가 보관한 값으로 URL 구성 → LLM hallucination 없음.
    - LLM 이 안 쓴(배치 안 한) 그림은 끝 '## 그림' 섹션에 보충해 손실 방지.
    figures 없으면 body 그대로.
    """
    # figure 유무와 무관하게 LLM 이 따로 쓴 캡션 평문 줄 제거 (우리 *이탤릭* 캡션과 중복 방지)
    body = _strip_llm_figure_captions(body)

    if not figures_kr:
        # 쓸 figure 가 없으면(예: pypdf 추출이라 caption 이 'page N' 쓰레기 → retriever 가
        # 전부 제외) 본문에 남은 [FIGN] placeholder 를 제거한다. 안 그러면 '[FIG2]' 같은
        # 평문이 위키에 그대로 노출된다. (진짜 그림은 PDF Docling 재처리 후 재합성으로 복구.)
        body = _FIG_LABEL_RE.sub("", body)
        return re.sub(r"\n{3,}", "\n\n", body).strip() + "\n"

    used: set[int] = set()

    def _repl(m) -> str:  # noqa: ANN001
        idx = int(m.group(1)) - 1
        if idx < 0 or idx >= len(figures_kr):
            return ""                       # 잘못된 라벨 → 제거
        if idx in used:
            return ""                       # 같은 [FIGN] 중복 → 이미지 2번 방지(첫 1회만)
        md = _figure_markdown(figures_kr[idx])
        if md is None:
            return ""
        used.add(idx)
        # 앞뒤 빈 줄로 블록 분리 (단독 이미지 줄로 렌더되게)
        return f"\n\n{md}\n\n"

    body = _FIG_LABEL_RE.sub(_repl, body)

    # 안 쓰인 그림 보충 (우선순위는 retriever 정렬 그대로) — 끝에 ## 그림 으로.
    leftover = [f for i, f in enumerate(figures_kr) if i not in used]
    if leftover:
        body = _append_figures(body, leftover)
    # 치환으로 생긴 과도한 빈 줄 정리 (3+ → 2)
    body = re.sub(r"\n{3,}", "\n\n", body).strip() + "\n"
    return body


def _parse_numbered_captions(text: str, expected: int) -> list[str] | None:
    """'1. ...\n2. ...' 형식 LLM 출력에서 번호별 한글 캡션 추출.

    줄 수가 expected 와 정확히 일치할 때만 채택(번호 어긋남 방지) — 안 맞으면 None
    반환해 caller 가 원본 절단 폴백을 쓰게 한다. 번호 접두("3. ", "3) ")는 제거.
    """
    if not text:
        return None
    items: list[str] = []
    for line in text.split("\n"):
        s = line.strip()
        if not s:
            continue
        # "12. ", "12) ", "12 - " 같은 선두 번호 제거
        i = 0
        while i < len(s) and s[i].isdigit():
            i += 1
        if i > 0 and i < len(s) and s[i] in ".)-":
            s = s[i + 1:].strip()
        if s:
            items.append(s)
    if len(items) != expected:
        return None
    return items


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
    # de-clone (2026-06-04): self-wiki 면 **정체성 item 만** 깊이 인용하고 cross-link 자료
    # (다른 정체성의 논문/개념)는 listing(관련 자료)로만. 같은 PDF 를 cross-link 한 여러
    # self-wiki 가 전부 그 논문 요약으로 복제되던 문제 차단 — 각 위키가 자기 자료 중심.
    identity = wiki_context.get("identity_item") or {}
    identity_id = identity.get("item_id")
    deep_identity = [s for s in sources if s["item_id"] == identity_id] if identity_id else []
    if deep_identity:
        deep_sources = deep_identity
        listing_only_sources = [s for s in sources if s["item_id"] != identity_id]
    else:
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


def _figures_block(figures_kr: list[dict[str, Any]]) -> str:
    """프롬프트의 <figures> 블록 — LLM 이 본문 맥락 속에 배치할 그림 목록.

    i 번째 figure → [FIG{i+1}] 라벨 + 한글 캡션. file_hash 는 노출하지 않는다(코드가 보관,
    생성 후 _insert_inline_figures 가 라벨을 실제 이미지로 치환). 그림 없으면 안내 문구.
    """
    if not figures_kr:
        return "(사용 가능한 그림 없음 — 그림 라벨을 쓰지 마세요)"
    lines: list[str] = []
    for i, f in enumerate(figures_kr, start=1):
        cap = (f.get("caption") or "").strip() or "(캡션 없음)"
        lines.append(f"[FIG{i}] {cap}")
    return "\n".join(lines)


def _build_paper_user_message(
    template: str, wiki_context: dict[str, Any], figures_kr: list[dict[str, Any]],
    paper_body_override: str | None = None,
) -> str:
    """writer_paper_v1.yaml 의 user_template 채우기 (재설계 element B/G).

    핵심은 primary 논문의 raw markdown 발췌(<paper_body>) — summary 가 아닌 실제 본문.
    <figures> 에 [FIGN] 라벨+한글캡션을 줘 LLM 이 본문 맥락 속에 그림을 배치하게 한다.
    나머지 source 는 보조 맥락으로 짧은 listing 만 (token 예산 보호 — Gemma 16384).

    paper_body_override: 큰 논문일 때 writer 가 map 압축한 노트(또는 raw 통째)를 넘김.
    None 이면 retriever 가 준 짧은 excerpt 사용(하위 호환).
    """
    pr = wiki_context.get("primary_raw") or {}
    all_sources = wiki_context["sources"]
    primary_item_id = pr.get("item_id")
    body_text = (
        paper_body_override if paper_body_override is not None
        else (pr.get("excerpt") or "")
    )

    # 보조 source listing (primary 제외, 상위 몇 개 제목·요약만)
    other_lines: list[str] = []
    n = 0
    for s in all_sources:
        if s.get("item_id") == primary_item_id:
            continue
        if n >= 6:
            break
        title = s.get("title") or (s.get("item_id") or "")[:8]
        line = f"- {title} ({s.get('source_type')})"
        if s.get("summary"):
            line += f": {s['summary'][:160]}"
        other_lines.append(line)
        n += 1
    sources_block = "\n".join(other_lines) if other_lines else "(추가 자료 없음)"

    user_notes = wiki_context.get("user_notes_combined") or "(no user notes)"
    if len(user_notes) > 500:
        user_notes = user_notes[:500] + "... (생략)"

    return template.format(
        paper_title=pr.get("title") or wiki_context["page"].get("title") or "(제목 미상)",
        paper_source_type=pr.get("source_type") or "paper",
        paper_url=pr.get("source_url") or "(no url)",
        paper_body=body_text or "(본문 발췌 없음)",
        figures_block=_figures_block(figures_kr),
        source_count=len(all_sources),
        sources_block=sources_block,
        user_notes_block=user_notes,
    )


def _enforce_input_budget(
    template: str, wiki_context: dict[str, Any], figures_kr: list[dict[str, Any]],
    system_msg: str, user_msg: str, paper_body: str, output_tokens: int,
) -> str:
    """최종 안전 가드 — system+user 추정 토큰이 context 예산을 넘으면 paper_body 를
    잘라 user_msg 를 재조립한다. map 압축으로도 안 줄어든 극단적 논문(수백 페이지)의
    최후 방어. 정상 경로(map 압축이 예산 안)는 그대로 통과(추가 비용 0)."""
    budget = _MODEL_CONTEXT_TOKENS - output_tokens - _PROMPT_SAFETY_MARGIN
    if _estimate_tokens(system_msg) + _estimate_tokens(user_msg) <= budget:
        return user_msg
    # user_msg 에서 paper_body 외 고정부 토큰 = 전체 - body
    fixed_tokens = _estimate_tokens(user_msg) - _estimate_tokens(paper_body)
    allowed_body_tokens = max(
        500, budget - _estimate_tokens(system_msg) - fixed_tokens,
    )
    allowed_chars = int(allowed_body_tokens * _CHARS_PER_TOKEN)
    truncated = (
        paper_body[:allowed_chars].rstrip() + "\n\n[... 길이 제한으로 이하 생략 ...]"
    )
    logger.warning(
        "paper input 예산 초과 — paper_body %d→%d자 truncate (page=%s)",
        len(paper_body), len(truncated), (wiki_context.get("page") or {}).get("slug", "?"),
    )
    return _build_paper_user_message(
        template, wiki_context, figures_kr, paper_body_override=truncated,
    )
