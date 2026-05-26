# D10 — llm_wiki 아키텍처 설계

> 작성 2026-05-26 (D10 첫 세션). 이 문서는 **설계 초안 + 결정 필요 사항** 문서.
> 사용자 검토 후 결정된 부분만 코드로 들어간다. 모든 변경은 §10 의 단계별 wave 로 진행.

---

## 0. 동기 — 기존 검색·키워드의 한계 (wiki 전환의 진짜 이유)

사용자 명시 (2026-05-26): **"지금 기존 아키텍처는 검색도 제대로 안 되고 키워드도 이상해. 그래서 wiki 타입으로 전환하는 거야. 제대로 만들었으면 좋겠어."**

이는 단순 새 layer 추가가 아니라 **검색·키워드 패턴 자체의 fundamental 재설계** 가 wiki 전환의 핵심 동기임을 의미.

### 0.1 기존 검색의 문제 — 메모 [[project-search-quality-issue]]

현재 [backend/api/search.py:80-86](backend/api/search.py#L80-L86):
- Qdrant chunk top-k vector search → item 단위 dedup
- chunk 단위 매칭이라 **PDF 의 garbage chunk (수학식/LaTeX 추출 실패)** 가 cosine 낮춤
- 실 사례 (2026-05-25 진단): "generative model summary" 쿼리 → LinkedIn item (score 0.635) 이 정작 깊은 PDF item (score 0.527) 보다 위
- summary 는 정상 한국어인데 chunk 에 안 들어가서 검색 무력화

이건 chunk-level retrieval 의 본질적 한계 — chunk 가 garbage 면 wiki 전체가 잠긴다.

### 0.2 기존 키워드 (categories / tags) 의 문제

- `items.tags`: LLM 자동 해시태그, 같은 의미 다른 표기 난립 (`#3DGS`, `#gaussian-splatting`, `#3D-GS`)
- `categories` (wave-4): items.tags 빈도 ≥ 3 휴리스틱 자동 생성 → 빈도 낮은 진짜 중요 concept 누락, 의미 cross-cutting (예: "auto-regressive" 는 LLM + Video gen 둘 다) 인식 X
- 결과: 66 카테고리 중 일부만 의미 일치 + 23,940 topic 의 분류가 들쭉날쭉

### 0.3 wiki 전환이 해결하는 것

| 기존 한계 | wiki 가 해결 |
|---|---|
| chunk top-k 가 garbage 에 흔들림 | **wiki 페이지 단위 검색** (페이지 본문은 agent 가 합성한 깨끗한 markdown) |
| summary 가 검색 안 됨 | wiki body 자체가 합성된 요약 — 페이지 본문 검색 = 의미 단위 검색 |
| categories 휴리스틱 한계 | **classifier agent 의 의미 클러스터링** (embedding + LLM 판정) → wave-4 휴리스틱 진화 |
| 키워드 cross-cutting 인식 X | 한 item 이 여러 wiki page 에 link 가능 (다중 modality) |
| 검색·응답이 매번 재합성 (RAG 한계) | wiki body 가 영구 누적 artifact — citation·cross-link 이미 있음 |

### 0.4 "제대로" 의 기준

사용자 표현 — "제대로 만들었으면 좋겠어". 임시 layer 추가 X, 정렬되고 확장 자연스러운 fundamental 재설계.

- ✅ 확장성: wiki_pages 1:N (한 topic → 여러 페이지), 버전 히스토리, SaaS multi-tenant 자연
- ✅ 검색 흐름: chunk top-k 와 공존하는 게 아니라, **wiki 페이지 단위가 1급 검색 layer** (chunk 는 fallback)
- ✅ 키워드 진화: classifier 가 wave-4 휴리스틱 categories 를 의미 클러스터링 categories 로 진화 (또는 wiki 페이지 색인 으로 흡수)
- ✅ 학습 후크: 각 agent 호출 + body diff 가 학습 신호로 적립 (Phase 4 sVLL)
- ✅ 단계: 4 agent 골격 한 번에 만들고 (skeleton 포함), backfill 만 점진

---

## 1. 왜 llm_wiki 인가 — 일반 RAG 와의 차이

### 1.1 LinkMind 의 현재 `/ask` (= 일반 RAG 패턴)

[backend/api/ask.py](backend/api/ask.py) 를 보면 매 질문마다:

1. Qdrant 에서 chunk top_k 벡터 검색 ([backend/api/search.py:80-86](backend/api/search.py#L80-L86))
2. item 단위 dedup → 각 item 의 `summary` (한국어 500-1500자) + chunk snippet 으로 context block 합성 ([backend/api/ask.py:46-71](backend/api/ask.py#L46-L71))
3. LLM 한 번 호출 → 답변 텍스트 ([backend/api/ask.py:78-84](backend/api/ask.py#L78-L84))

이게 NotebookLM / ChatGPT file upload / 대부분 RAG 의 패턴. 동작은 한다. 단점은 karpathy 가 정확히 짚었다:

> "the LLM is rediscovering knowledge from scratch on every question. There's no accumulation."

같은 주제 5번 물으면 LLM 이 매번 같은 chunks 를 다시 읽고 다시 합성. 누적 X.

### 1.2 llm_wiki 가 다른 점

핵심 통찰 — **wiki 가 영구 누적 artifact**:

| 일반 RAG | llm_wiki |
|---|---|
| query 시 chunk retrieve → LLM 합성 | wiki 페이지 (이미 합성됨) → LLM 읽기 |
| 매번 처음부터 | 한 번 합성 후 점진 진화 |
| cross-reference 없음 | wiki 페이지 간 link 가 자료의 핵심 |
| contradictions 매번 재발견 | contradictions 가 페이지 본문에 사전 명시 |
| 사용자 질문 시 발견된 통찰 휘발 | 답변이 wiki 페이지로 filing-back 가능 |

> "the wiki is a persistent, compounding artifact. The cross-references are already there. The contradictions have already been flagged."

### 1.3 LinkMind 의 §1 학습 비전과의 정합

§1: "지속적 재학습 continuous training loop". 즉 사용자 행동이 **학습 신호** 로 적립되어야 함. llm_wiki 패턴은 이 비전과 정확히 맞물림:

- 사용자가 wiki 페이지에 추가한 메모 (user_notes) → 학습 신호
- 사용자가 wiki 페이지 간 만든 link → 학습 신호
- agent 가 자동으로 filing-back 한 새 페이지 → 모델이 합성한 지식 → 자기 학습 가능
- writer agent 가 만든 markdown 본문 → 다음 모델 round 의 reference

일반 RAG 는 매번 휘발이라 학습 신호 적립이 어렵다. wiki 패턴이 §1 의 토양.

---

## 2. karpathy 패턴 요약 (3 + 3 + 2)

원본: [external/karpathy/llm_wiki](external/karpathy/llm_wiki) (gitignored — MIT, idea-only 문서).

### 2.1 Three layers

| layer | 의미 | LinkMind 매핑 |
|---|---|---|
| **Raw sources** | immutable, LLM 이 읽기만 함, source of truth | `items` (raw-first §2 보장) ✅ |
| **Wiki** | LLM-owned markdown, 합성·갱신·cross-ref | `topics` (의미 단위) — 본문 markdown 누락 🟡 |
| **Schema** | LLM 의 운영 규칙 (CLAUDE.md, AGENTS.md) | `CLAUDE.md` + `docs/` ✅ |

### 2.2 Three operations

| op | karpathy | LinkMind 현재 | gap |
|---|---|---|---|
| **Ingest** | raw → wiki 페이지 합성·갱신 | raw → `auto_link_topics` (topic link 만) | wiki 본문 합성 X |
| **Query** | wiki 탐색 + LLM 종합 + citation | Qdrant chunks → LLM 합성 | wiki 페이지 단위 X |
| **Lint** | 모순·stale·orphan 정기 점검 | 없음 | 신규 |

### 2.3 Two special files

| file | karpathy | LinkMind 매핑 |
|---|---|---|
| **index.md** | content catalog | `/graph/categories` endpoint + frontend TopicsTree ✅ (UI 형태 다름) |
| **log.md** | chronological | `items.ingested_at` + git log ✅ (별 파일 없음) |

---

## 3. LinkMind 매핑 — 이미 갖춘 것 vs 부족한 것

### 3.1 갖춘 것 (재활용)

- **Raw sources** — `items` 16,463건 (raw_content NOT NULL, raw-first §2)
- **topic 단위 의미 클러스터링** — `topics` 23,940 + `item_topics` 29,225 ([backend/db/schema.sql:208-244](backend/db/schema.sql#L208-L244))
- **category 색인** — `categories` 66 + `topic_categories` 927 ([backend/db/schema.sql:305-338](backend/db/schema.sql#L305-L338))
- **요약** — items.summary (한국어 500-1500자, qwen2.5-7B 로 생성, [backend/jobs/analysis_worker.py](backend/jobs/analysis_worker.py))
- **embedding 검색 인프라** — Qdrant bge-m3, 99,572 chunks (D13 vLLM-embed)
- **multi-modality 첨부** — attachments 14,937 (PDF figures, YouTube thumbnails)
- **3D graph UI** — react-force-graph-3d ([frontend/components/GraphView.tsx](frontend/components/GraphView.tsx))
- **사용자 메모** — items.user_notes (raw-first 의 핵심 학습 신호)
- **delete UI** — D12 wave-3 영구 삭제 + raw 보존
- **LLM provider 추상화** — vLLM Qwen2.5-7B 7초 응답 (wave-4)

### 3.2 부족한 것 (D10 scope)

| 부족 | 의미 |
|---|---|
| **wiki 페이지 본문 (long-form markdown)** | `topics.description` 은 짧은 한 줄 설명. wiki 페이지의 합성된 본문 (multi-modality 섹션, citation, cross-link) 가 들어갈 자리 없음 |
| **incremental 합성·갱신** | 새 item 이 들어와도 기존 topic 의 description 갱신 X. 매번 raw 만 link |
| **agent orchestration** | 단일 LLM 호출 패턴. classifier / retriever / writer / critic 역할 분리 X |
| **자동 분류 (classifier)** | wave-4 categories 는 items.tags 빈도 ≥ 3 휴리스틱. agent 가 의미 단위로 모든 자료 (placeholder 포함) 자동 분류 X |
| **wiki 페이지 단위 query** | `/wiki/{slug}` endpoint 없음. wiki 페이지 그대로 보여주는 view 없음 |
| **lint** | 모순·stale·orphan 점검 없음 |
| **filing-back** | 사용자 query 답변을 wiki 페이지로 적립 X |

---

## 4. 핵심 설계 결정 (사용자 confirm 필요)

이 결정들이 다음 wave 들의 토대. 한 번에 정하고 가야 일관된 구현 가능.

### 결정 1 — wiki 페이지 본문 저장 형태 — ✅ **B 채택** (2026-05-26 사용자 결정)

| 옵션 | 장점 | 단점 |
|---|---|---|
| A. `topics.body` column 추가 | schema 변경 최소, 1:1 단순 | 1:N 확장 시 schema migration 큼, 버전 히스토리 X |
| **✅ B. 별 `wiki_pages` 테이블** | 한 topic → 여러 페이지 가능 (초보자/심화/FAQ), **버전 히스토리** 자연 (학습 데이터 신호), wiki 전용 메타 확장 자유 | 초기 구현 복잡도 약간 증가 |
| C. filesystem markdown | git diff, Obsidian 호환 | SaaS multi-tenant 곤란, DB↔FS 동기화 부담 |

**채택 이유 — 확장성 우선**:
- agent 가 body 계속 합성하는 모델 → 매 합성마다 이전 버전 보존하면 **학습 데이터 (`prev_body` → `new_body` diff = preference signal)** 자동 적립. §1 학습 비전과 정합
- 1:N 자연 — 같은 topic 에 "한국어/영어", "초보/심화", "사용자별 view" 등 미래 확장 자유
- SaaS multi-tenant 자연 (`wiki_pages.tenant_id` 만 추가)
- A 로 시작했다가 B 로 옮기는 비용 > 처음부터 B 의 비용

### 결정 2 — wiki 페이지 본문 갱신 시점 — ✅ **C → A 단계 전환** (2026-05-26 사용자 결정)

| 옵션 | 의미 |
|---|---|
| A. 매 ingest 마다 자동 | 항상 최신, LLM 비용 폭발 위험 |
| B. 주기 batch | 비용 예측 가능, 사용자 시점에 stale 가능 |
| **✅ C → A 단계 전환** | wave-1 lazy (사용자가 wiki page 열 때만 합성), 안정화 후 자동 ingest |

**채택 이유**:
- 23,940 topic 중 실제 사용자가 보는 건 일부 — 무조건 합성은 낭비
- classifier 비용/품질 검증 (wave-1b) 후 자동 전환 결정
- ingest → `body_status='stale'` 마킹 → 다음 GET 에서 lazy regenerate 흐름이 비용/UX 균형

### 결정 3 — agent orchestration 프레임워크

| 옵션 | 장점 | 단점 |
|---|---|---|
| **A. 자체 sequential** (단순 함수 호출) | 의존성 최소, 디버그 쉬움, vLLM 직접 호출 | structured tool-use / multi-step plan 한계 |
| **B. langchain / langgraph** | 표준 패턴, multi-agent graph 자연 | 의존성 무거움, 추상화 과함 (MVP §6 위반 위험) |
| **C. hermes-agent style auto-skills** | 자가학습 후크 자연, vendor 가능 (MIT) | 학습 곡선, 우리 구조와의 정합 검증 필요 |

**추천: A**. 이유:
- §6 MVP 원칙 — 추상화 금지. 4 agent 면 framework 없이 충분
- 자체 구현이면 §11 ai_agents/ ↔ backend HTTP 책임 분리 패턴 그대로 유지 가능
- 나중에 langgraph 가 필요해지면 그때 도입 (지금 미리 X)

### 결정 4 — classifier 가 wave-4 categories 재구성하는지

사용자 비전 (memory): "agent 가 모든 자료 자동 wiki 분류 → wave-4 휴리스틱 categories 진화".

| 옵션 | 의미 |
|---|---|
| **A. 새 wiki_pages 만 만들고 categories 는 그대로** | 안전, rollback 쉬움, 두 layer 공존 |
| **B. classifier 가 categories 도 재구성 (synonyms 의미 클러스터링)** | 진정한 진화, 의미 일치 |
| **C. categories 를 wiki_pages 와 1:1 매핑 (categories → wiki page 의 인덱스)** | karpathy 의 index.md 정신 — categories 는 wiki 색인 |

**추천: 단계적 — 1단계 A → 2단계 C**:
- D10 wave-1: 새 wiki layer 만 도입 (A) — 기존 categories 보존, 두 layer 공존하며 비교
- D10 wave-3 (검증 후): categories 를 wiki 색인 으로 재정의 (C) — categories.slug = wiki page slug 매핑

### 결정 5 — 첫 prototype scope (wave-1)

| 옵션 | 범위 | 소요 |
|---|---|---|
| **A. 가장 작게** — schema 추가 + writer agent 1 개 + `/wiki/{slug}` GET only (lazy 합성) | 한 wave (1-2 세션) | 검증 빠름 |
| **B. 중간** — schema + classifier + writer + retriever + `/wiki/{slug}` (GET + POST regenerate) | 2-3 wave | 4 agent 중 critic 만 빠진 형태 |
| **C. 전체** — 4 agent + classifier 가 16,463 items 백필 + filing-back + lint | 5+ wave | risky, 검증 없이 큼 |

**추천: A**. 이유:
- karpathy 의 정신 — "incrementally" 갖춰가기
- writer agent 한 개라도 동작 보이면 패턴 검증 가능
- classifier 는 무거움 (16,463 items 처리) — 첫 wave 는 사용자가 수동으로 wiki 페이지 선택해서 합성 trigger

---

## 5. 4 agent 구조 (제안)

위 결정 후 확정. 아래는 plan.

### 5.1 모듈 위치 — `backend/agents/`

```
backend/agents/
├─ __init__.py
├─ base.py              # AgentBase ABC + AgentContext + AgentResult
├─ classifier.py        # ClassifierAgent  — items → wiki 페이지로 자동 클러스터링
├─ retriever.py         # RetrieverAgent   — wiki 페이지 + 그 자료 통합 검색
├─ writer.py            # WriterAgent      — wiki 페이지 본문 합성 (multi-modality)
└─ critic.py            # CriticAgent      — 출처 검증 + placeholder 부족 명시
```

**원칙** (§11 책임 분리):
- agent 는 backend 내부 모듈 — `LLMProvider` 직접 호출 OK (`ai_agents/` 와 다름, 그쪽은 외부 daemon)
- 모든 agent 호출은 `prompts` 테이블에 version 등록된 prompt 사용 (§2 Versioned analysis)
- 각 agent 의 output 은 trace 가능 — `agent_runs` 테이블에 input/output/model/version/duration 적립 (Phase 4 학습 데이터)

### 5.2 각 agent 역할

#### ClassifierAgent

- **입력**: 신규 ingest 된 item (또는 기존 item 16,463 backfill)
- **처리**: item 의 raw_content + summary + tags + (placeholder 인 경우 user_notes) → embedding 기반 가까운 기존 wiki 페이지 후보 retrieve → LLM 으로 "이 item 이 어느 wiki 페이지에 속하는가?" 분류 → 매칭 없으면 새 페이지 생성 제안
- **출력**: `wiki_page_id` (기존) 또는 새 페이지 metadata (label, slug, description)
- **호출 모델**: Qwen2.5-7B (vLLM, 빠른 결정용)
- **호출 시점**: ingest 후 BackgroundTask (현재 `analysis_worker` 패턴)

#### RetrieverAgent

- **입력**: wiki 페이지 slug 또는 query 텍스트
- **처리**: 페이지 metadata → 속한 items 통합 (item_topics + topic_categories 활용) → 각 item 의 summary + 첨부 + user_notes 종합
- **출력**: WikiContext (페이지 본문 합성에 필요한 raw materials)
- **호출 모델**: 없음 (DB + embedding 만)

#### WriterAgent

- **입력**: WikiContext + 기존 wiki body (있으면)
- **처리**: 다음 구조의 markdown 합성:
  ```markdown
  # {title}

  > {description — 한 단락 요약}

  ## Sources
  - [{item.title}]({item.source_url}) — {source_type, summary 한 줄}
  - ...

  ## Synthesis
  {LLM 이 종합한 본문 — citation 포함}

  ## User notes
  {사용자가 추가한 메모 통합}

  ## Open questions / contradictions
  {critic agent 가 flag 한 부분}

  ## Cross-links
  - [[other-wiki-slug]] — 관련 페이지
  ```
- **출력**: markdown body
- **호출 모델**: Qwen2.5-7B (긴 합성 — 8K~16K context)
- **호출 시점**: lazy (`/wiki/{slug}` 첫 호출) → Phase 후반 자동

#### CriticAgent

- **입력**: WikiContext + 합성된 body
- **처리**: citation 검증 (page 의 모든 claim 이 sources 에 있는지), placeholder 자료의 정보 부족 명시, 시계열 모순 (오래된 → 새 자료 우선) flag
- **출력**: 수정 patches 또는 "Open questions" 섹션 추가
- **호출 모델**: Qwen2.5-7B
- **호출 시점**: writer 다음 sequential

---

## 6. `/wiki/{slug}` endpoint 윤곽

### 6.1 GET `/wiki/{slug}` — wiki 페이지 view

```http
GET /wiki/lora-fine-tuning
→ 200 OK
{
  "slug": "lora-fine-tuning",
  "title": "LoRA Fine-tuning",
  "description": "Low-Rank Adaptation — pretrained model 의 일부 weight 만 학습...",
  "body": "# LoRA Fine-tuning\n\n> Low-Rank...\n\n## Sources\n- ...",
  "body_generated_at": "2026-05-26T10:00:00Z",
  "body_model": "qwen2.5-7b",
  "body_prompt_version": "wiki_writer_v1",
  "source_count": 12,
  "sources": [
    { "item_id": "...", "title": "...", "source_type": "pdf", "source_url": "..." }
  ],
  "cross_links": ["pytorch-fundamentals", "transformer-architecture"]
}
```

**처리**:
1. `topics WHERE slug = $1` 으로 페이지 메타 조회
2. `body` 가 있으면 그대로 반환
3. 없거나 stale (사용자가 "regenerate" 옵션) 이면 → RetrieverAgent → WriterAgent → CriticAgent → save → 반환

### 6.1.5 POST `/wiki/search` — wiki 페이지 단위 검색 (§0.3 의 핵심)

```http
POST /wiki/search
Content-Type: application/json
{ "query": "generative model summary", "top_k": 10 }
→ 200 OK
{
  "query": "...",
  "hits": [
    {
      "page_id": "...",
      "slug": "diffusion-models",
      "title": "Diffusion Models",
      "score": 0.82,
      "body_excerpt": "...",
      "source_count": 12,
      "matched_in": "body"          // body | description | sources
    }
  ]
}
```

**처리** — 검색 layer 가 chunk top-k 가 아닌 **wiki body embedding** 우선:
1. wiki body 의 embedding (별 column `wiki_pages.body_embedding`) 으로 cosine top_k
2. body 가 비어있는 (lazy 아직 안 만든) 페이지는 description + sources 의 summary 합쳐서 fallback
3. chunk top-k 는 fallback (PDF 의 garbage chunk 문제 §0.1 회피)
4. 기존 `/search` endpoint 는 그대로 유지 (chunk-level 도 필요한 경우 대비) 하되 frontend 기본은 `/wiki/search` 로 전환

### 6.1.6 POST `/ask` 의 진화

```
기존: question → chunk top-k → context block → LLM
신규: question → /wiki/search top-k → 해당 wiki body 들 → LLM 종합
```

wiki body 가 이미 합성되어 있어 chunk snippet 보다 정보 밀도 ↑↑. RAG 흐름 자체가 wiki 위에서 동작.

### 6.2 POST `/wiki/{slug}/regenerate` — body 재합성

명시적 trigger. 사용자 행동:
- "사용자 새 메모 추가했으니 wiki 갱신해줘"
- "이 페이지 별로네, 다시 써줘"

### 6.3 POST `/wiki/classify` — classifier 수동 trigger

미분류 items 일괄 classifier 돌리기. 첫 사용자 행동:
- "지금까지 16,463 items 가 placeholder + 일부 topic 만 link 됐는데, wiki 페이지로 다 분류해줘"

이건 무거우니까 BackgroundTask + 진행률 endpoint (`/wiki/classify/status`).

---

## 7. schema 변경 안 (결정 1B 채택)

### 7.1 신규 `wiki_pages` 테이블 (한 topic → 여러 페이지 가능, 버전 히스토리)

```sql
-- 한 wiki page = 한 topic 의 한 버전 합성. 같은 topic 에 여러 page 가능
-- (예: 한국어/영어, 초보자/심화, 사용자별 view — 미래 확장).
CREATE TABLE IF NOT EXISTS wiki_pages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- topic 이 있으면 link (대부분 케이스), 없는 stand-alone wiki page 도 가능 (lint 결과 등)
    topic_id UUID REFERENCES topics(id) ON DELETE CASCADE,

    slug    TEXT UNIQUE NOT NULL,            -- 'lora-fine-tuning' (URL)
    title   TEXT NOT NULL,                   -- 'LoRA Fine-tuning'
    description TEXT,                        -- 한 단락 요약

    -- 본문 (markdown, agent 가 합성)
    body                  TEXT,
    body_model            TEXT,               -- 'qwen2.5-7b'
    body_prompt_version   TEXT,               -- 'wiki_writer_v1'
    body_generated_at     TIMESTAMPTZ,
    body_status           TEXT NOT NULL DEFAULT 'empty',
                                              -- 'empty' | 'generating' | 'ready' | 'stale'

    -- 검색용 body embedding (§6.1.5 wiki 단위 검색의 핵심)
    body_embedding_model  TEXT,               -- 'BAAI/bge-m3'
    body_embedding_dim    INTEGER,
    -- 실제 vector 는 Qdrant 별 컬렉션 'wiki_pages' 에 (chunks 와 분리)

    -- variant (같은 topic 에 여러 페이지 구분, 1 page = 1 variant)
    variant TEXT DEFAULT 'default',           -- 'default' | 'beginner' | 'advanced' | 'ko' | ...

    -- 사용자 메모 / 편집 (Phase 후반 wiki 편집 UI)
    user_overrides TEXT,                      -- 사용자가 직접 수정한 부분 (보존 — 다음 합성 시 merge)
    is_pinned BOOLEAN NOT NULL DEFAULT FALSE,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (topic_id, variant)                -- 한 topic 에 같은 variant 는 1개만
);

CREATE INDEX IF NOT EXISTS idx_wiki_pages_topic  ON wiki_pages(topic_id);
CREATE INDEX IF NOT EXISTS idx_wiki_pages_status ON wiki_pages(body_status);

DROP TRIGGER IF EXISTS wiki_pages_set_updated_at ON wiki_pages;
CREATE TRIGGER wiki_pages_set_updated_at
    BEFORE UPDATE ON wiki_pages
    FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();
```

### 7.1.5 신규 `wiki_page_versions` 테이블 (학습 데이터 적립)

```sql
-- 매 body 합성마다 이전 버전 보존 — Phase 4 학습 데이터 (preference signal):
-- (prev_body, new_body, user_kept_or_modified) 가 fine-tune 학습 입력.
CREATE TABLE IF NOT EXISTS wiki_page_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    page_id UUID NOT NULL REFERENCES wiki_pages(id) ON DELETE CASCADE,

    version_number INTEGER NOT NULL,          -- 1 부터 증가
    body TEXT NOT NULL,
    body_model TEXT NOT NULL,
    body_prompt_version TEXT NOT NULL,
    agent_run_id UUID,                        -- agent_runs.id (이 합성을 trigger 한 run)

    -- 합성 trigger 이유 (학습 신호)
    trigger_reason TEXT,                       -- 'first_gen' | 'stale_regenerate' | 'user_request' | 'lint_fix'

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (page_id, version_number)
);

CREATE INDEX IF NOT EXISTS idx_wiki_page_versions_page
    ON wiki_page_versions(page_id, version_number DESC);
```

### 7.1.6 topics 와의 관계

`topics` 는 그대로 유지 (의미 단위). `wiki_pages` 가 topic 위의 합성 layer.
- 한 topic 에 default variant 1 페이지가 기본 (1:1 처럼 작동)
- 미래 확장: 한 topic 에 multiple variant 추가 가능

`auto_link_topics` 흐름은 그대로 — wiki page 는 lazy 생성 (사용자가 열 때).

### 7.1.7 신규 `wiki_page_items` 테이블 (M:N, 핵심) — 2026-05-26 사용자 질문 반영

**한 item 이 여러 wiki 페이지에 속함 (cross-cutting concern).** 예: LoRA 논문 → "LoRA Fine-tuning" + "Parameter-Efficient FT" + "Transformer Adaptation" 셋 다 link.

```sql
CREATE TABLE IF NOT EXISTS wiki_page_items (
    wiki_page_id UUID NOT NULL REFERENCES wiki_pages(id) ON DELETE CASCADE,
    item_id      UUID NOT NULL REFERENCES items(id)      ON DELETE CASCADE,

    -- classifier 가 매긴 매칭 강도
    confidence   REAL NOT NULL DEFAULT 1.0,         -- 0 ~ 1
    source       TEXT NOT NULL DEFAULT 'auto',      -- 'auto' (classifier) | 'manual' (사용자) | 'topic-inherited'

    -- 이 item 이 이 wiki 안에서의 역할
    role         TEXT,                              -- 'primary' | 'example' | 'context' | 'related-work'

    -- 학습 데이터 신호 — 사용자가 link 를 수동으로 제거/유지/pin
    user_action  TEXT,                              -- NULL | 'kept' | 'removed' | 'pinned'

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (wiki_page_id, item_id)
);

CREATE INDEX IF NOT EXISTS idx_wiki_page_items_page ON wiki_page_items(wiki_page_id);
CREATE INDEX IF NOT EXISTS idx_wiki_page_items_item ON wiki_page_items(item_id);
CREATE INDEX IF NOT EXISTS idx_wiki_page_items_conf ON wiki_page_items(confidence DESC);
```

**classifier 의 output 은 list (single 아님)**:
```json
[
  { "wiki_slug": "lora-fine-tuning",      "confidence": 0.95, "role": "primary" },
  { "wiki_slug": "parameter-efficient-ft", "confidence": 0.78, "role": "example" },
  { "wiki_slug": "transformer-adaptation", "confidence": 0.62, "role": "context" },
  { "wiki_slug": null, "new_page_proposal": { "label": "...", "description": "..." } }
]
```

threshold (예: confidence ≥ 0.5) 이상은 모두 link. **한 번의 LLM 호출로 여러 wiki 자동 분류** — 비용 ↓.

**사용자 override (학습 신호)**:
- frontend wiki 페이지에서 [✕] 누르면 `user_action='removed'` 저장 (DELETE 안 함 — 다음 합성 시 제외 + 학습 데이터 누적)
- [📌] 누르면 `user_action='pinned'` (이 자료는 이 wiki 에 중요)
- 사용자가 다른 wiki 에 수동 추가 → `source='manual'`, `user_action='pinned'`
- 모두 Phase 4 classifier LoRA 학습의 preference signal

### 7.2 `agent_runs` 테이블 (학습 데이터 적립)

```sql
CREATE TABLE IF NOT EXISTS agent_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_name      TEXT NOT NULL,     -- 'classifier' | 'retriever' | 'writer' | 'critic'
    agent_version   TEXT NOT NULL,     -- prompt version
    llm_model       TEXT NOT NULL,
    input_summary   TEXT,              -- 입력 핵심 (인덱스용)
    input_full      JSONB,             -- 전체 입력 (학습 데이터)
    output_text     TEXT,
    output_meta     JSONB,
    duration_ms     INTEGER,
    error           TEXT,
    related_item_id   UUID REFERENCES items(id)  ON DELETE SET NULL,
    related_topic_id  UUID REFERENCES topics(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

§1 학습 비전 — 각 agent 호출이 학습 데이터로 적립. Phase 4 LoRA 학습 입력.

### 7.3 (Phase 후반) `wiki_cross_links`

cross-link 가 본문 markdown 안에 `[[slug]]` 로 들어가도 OK. 별 테이블은 필요해지면 추가.

---

## 7.2 신규 자료 → 기존 wiki 자동 확장 (compounding artifact, 핵심)

karpathy 의 "the wiki keeps getting richer with every source you add" — LinkMind 의 결정적 차별점.

### 흐름

```
새 item ingest
   │
   ▼
classifier 호출 (한 번)
   │
   ▼
매칭된 모든 wiki_page (여러 개) 에 wiki_page_items insert
   │
   ▼
매칭된 wiki_pages.body_status = 'stale' (자동)
   │
   ▼
사용자가 그 wiki page 열 때 lazy 재합성
  - writer 가 신규 자료까지 종합한 새 body 생성
  - 옛 body 는 wiki_page_versions 에 version+1 로 보존
  - critic (wave-3): contradiction 감지 시 body 에 명시
   │
   ▼
cross-link 자동 진화
  - 신규 item 이 여러 wiki 에 link 되면 → 그 wiki 들 사이 강도 +1
  - graph 에 edge 자연 형성 (예: "LoRA Fine-tuning" ↔ "Diffusion Models")
```

### 두 종류 확장

1. **자료 갯수 확장** — 한 wiki page 의 source 가 시간 누적 (단순)
2. **cross-link 진화** — 새 자료가 여러 wiki 에 link 되면 wiki 들 사이 연결 강화 (중요)

### 합성 시점 — **Eager 즉시** (2026-05-26 사용자 결정)

사용자 명시 — "그때그때 위키화해줘야 위키가 똑똑해진다." 이유:
- 매 ingest 가 wiki body 진화 → cross-link 즉시 형성 → 다른 wiki 와의 관계 자동 발견
- critic 가 즉시 contradiction 감지 → 새 통찰 발견 (wave-3)
- 학습 데이터 누적 빠름 — `wiki_page_versions` diff 매 ingest 마다 1개씩 적립
- 사용자 인지 — "wiki 가 살아있다" 가시화

**흐름**:
```
ingest → classifier link 즉시
      → 매칭된 모든 wiki page 마다 BackgroundTask 즉시 enqueue
      → vLLM 즉시 합성 (수 초 ~ 수십 초)
      → version+1 + body_status='ready'
      → 사용자 열 때 항상 ready ✓
```

**비용**:
- vLLM Qwen2.5-7B 이미 가동 (D13 인프라, GPU 18.5GB 상시 점유)
- 추가 LLM 호출 = GPU 시간만 (외부 API 비용 0)
- RTX 4090 sequential 처리 (1 회) → 자연 queue
- wave-2 에 **incremental patch** 도입 (옛 body + 신규 자료만 통합 = 합성 시간 짧음)
- burst (telegram backfill 등) 시 시간 누적 → 진행률 표시

### 학습 데이터 신호

- `wiki_page_versions` 의 version N → N+1 diff = "이렇게 진화" 학습 데이터
- `wiki_page_items.user_action` = 사용자 preference signal
- Phase 4 LoRA 학습 입력 — classifier 본인 데이터 학습

---

## 8. 자료 자동 분류 흐름 (ClassifierAgent)

### 8.1 신규 ingest 흐름

1. URL/YouTube/Slack/Telegram ingest → `items` insert
2. 기존 `auto_link_topics` 호출 (external_id 기반 link) — 그대로 유지
3. **신규**: BackgroundTask 로 ClassifierAgent 호출
   - external_id 로 이미 link 된 topic 있으면 → 그 topic 의 `body_status='stale'` 마킹
   - 없으면 → classifier 가 의미 가까운 기존 topic 후보 retrieve → 매칭 / 새 topic 생성
4. 사용자가 `/wiki/{slug}` 열면 lazy regenerate (writer + critic)

### 8.2 기존 16,463 items 백필 흐름

`POST /wiki/classify` 호출 → BackgroundTask 가 chunked 로 처리:

1. **1차 — external_id 그룹 우선**: 이미 `auto_link_topics` 가 처리한 그룹은 skip
2. **2차 — placeholder + orphan**: 4,000건 (placeholder 633 + image_no_ocr 1,751 + 기타) 을 classifier 가 의미 기반 분류
   - 사용자 비전 — placeholder 도 wiki 에 위치시켜 "이 wiki 의 자료 풀에 fetch 실패 표시" 로 보이게
3. **3차 — 일반 items**: 기존 topic 에 link 되지 않은 나머지

진행률은 `/wiki/classify/status` 로 polling.

---

## 9. Phase 4 학습 데이터 적립 후크

§14 + memory `project-next-session-entrypoint` — 자가학습 루프를 위한 적립 신호:

| 행동 | 적립 신호 |
|---|---|
| classifier 가 wiki page 매칭 / 새 페이지 생성 | `agent_runs` (input=item, output=page) |
| writer 가 본문 합성 | `agent_runs` (input=context, output=markdown) |
| critic 가 patch flag | `agent_runs` (input=body, output=patches) |
| 사용자가 wiki body 수정 (Phase 후반 wiki 편집 UI) | 수정 diff = preference signal |
| 사용자가 user_notes 추가 | 이미 적립 (items.user_notes) |
| 사용자가 wiki 페이지에 👍/👎 (Phase 후반) | feedback 테이블 (Phase 3 후반 plan) |

Phase 4 dataset exporter (`backend/jobs/export_training_data.py`) 가 위 신호 종합 → JSONL (LLaMA-Factory 포맷).

---

## 10. 단계별 wave plan (D10 scope) — 2026-05-26 재정렬 (사용자 "제대로" 요구 반영)

§0 동기 — 검색·키워드 fix 가 wiki 전환의 핵심 → wave-1 부터 4 agent **골격 (skeleton 포함)** 다 만들어 확장 자연. backfill 만 점진.

### ✅ D10 wave-1 — schema + 4 agent 골격 + 검색 layer 전환 (2026-05-26 완료)

**확장 가능한 골격** — 모든 agent 자리·DB 자리·endpoint 자리 잡고 작동 검증.

- ✅ 이 설계 문서 (사용자 검토 후 확정)
- ✅ migrate_schema: `wiki_pages` + `wiki_page_versions` + `wiki_page_items` (M:N) + `agent_runs` (4 테이블)
- ✅ Qdrant `linkmind_wiki_pages` 컬렉션 (body embedding, dim 1024 bge-m3) — chunks 와 분리
- ✅ `backend/agents/base.py` — AgentBase ABC (physics-intern state-centric) + `build_context()` + `run()` + `agent_runs` 자동 적립
- ✅ `backend/agents/prompts/{writer,classifier}_v1.yaml` — YAML loader (ml-intern 패턴)
- ✅ `backend/agents/retriever.py` — wiki page 의 source items + 첨부 + cross-link 후보 통합
- ✅ `backend/agents/writer.py` — wiki body markdown 합성 + body 에서 Sources/Cross-links/Keywords 섹션 제거 (frontend aside 가 DB 기반 표시)
- ✅ `backend/agents/classifier.py` — embedding 후보 + LLM JSON list output + parse retry + 새 wiki_page 'stale' 마킹
- 🟡 `backend/agents/critic.py` — **stub** (wave-3 에 본격 구현)
- ✅ `backend/api/wiki.py` — 8 endpoints: list / detail / regenerate / search (Qdrant) / classify / keywords (add-remove / autocomplete)
- ✅ frontend rename (frontend_v2 → frontend, 44 refs sed: CLAUDE/README/docs/scripts)
- ✅ `frontend/app/wiki/page.tsx` + `[slug]/page.tsx` + `components/wiki/{WikiBody,KeywordsEditor}.tsx`
- ✅ `backend/jobs/wiki_backfill_from_topics.py` — 23,940 topics → 23,852 wiki_pages + 29,161 wiki_page_items (1:1 옮김, ~5분)
- ✅ 실 데이터 smoke (LoRA / DINOv2 / DiffSplat / Gaussian Splatting 등 검증)

### ✅ D10 wave-2 — 자동화 + 키워드 + arxiv hook + 성능 (2026-05-26 완료)

- ✅ wave-2c: analysis_worker `_classify_to_wiki` hook — summary 후 classifier 자동 호출 → wiki_page_items 매핑 + stale 마킹
- ✅ wave-2d: keywords 진화:
  - schema `wiki_pages.keywords TEXT[]` + GIN index
  - writer prompt `## Keywords` 섹션 자동 추출 + `_parse_keywords_section` body 파싱
  - 3 API: POST `/wiki/{slug}/keywords` (add/remove) + GET `/wiki/_keywords/search` (autocomplete) + GET `/wiki/{slug}` 응답에 keywords 필드
  - frontend `KeywordsEditor` — view (pill click → matching wiki filter) / edit (✏️ 수정 토글 + ✕ 삭제 + + 추가 + autocomplete + ✨ 신규 등록)
  - `/wiki` list 의 keyword filter (URL `?keyword=X`) + chip UI
- ✅ arxiv URL hook — `backend/ingest/arxiv/__init__.py` (arxiv API export.arxiv.org/api/query) + `backend/ingest/url/__init__.py` 의 extract_doc 직후 hook. 신규 arxiv URL → 진짜 논문 제목 (예: arxiv:2003.02014 → "Redesigning SLAM for Arbitrary Multi-Camera Systems"). IEEE/DOI 도 자동 (HTML SSR + redirect)
- ✅ wiki body 구조 정리 (사용자 명시): 5 섹션 narrative 만 (#title / >TL;DR / ##무엇인가 / ##어떻게-왜 / ##사용-한계). Sources/Cross-links/Keywords 는 LLM 출력하되 writer.py 가 body 에서 제거 → frontend aside 가 DB 기반 표시 (중복 없음, 일관성)
- ✅ Relationship (옛 Cross-links 이름 변경) — 빈 섹션도 "(없음)" 명시
- ✅ daemon 분리 (사용자 정책 disjoint):
  - `wiki_writer_worker` (lifespan, **body_status='stale' 만**) — 신규 ingest 자동 wiki body 합성. classifier 가 새 wiki_page 도 'stale' 마킹 (즉시 trigger). env `LINKMIND_WIKI_WRITER_DAEMON=0` 으로 off
  - `wiki_writer_batch` CLI (empty + stale) — 사용자 직접 실행. `scripts/run_wiki_backfill.sh` shell wrapper
- ✅ scripts/run_wiki_backfill.sh — start (자동 SIGKILL 재시작) / stop / stop-force / restart / tail / status / foreground / dry-run / limit / slug / concurrency. PID lock + 이름 매칭 (좀비 잡음, bash command line false positive 회피)
- ✅ **성능 최적화 (4.6x)** — 사용자 친구 분석 받아 진행:
  - vLLM `--enable-prefix-caching` (system prompt 매번 cache hit)
  - `--max-num-batched-tokens 16384` + `--max-num-seqs 32`
  - writer max_tokens 2048 → 1024 (가끔 무한 list 잘림 방지)
  - batch CLI sequential → **asyncio.gather N=4 concurrent worker** (vLLM continuous batching 활용)
  - SKIP LOCKED row fetch (race-free) + WriterAgent per-task 새 인스턴스 + 새 SessionMaker
  - **결과**: page 당 17초 → 3.7초. ETA 4.7일 → ~1일

### D10 wave-3 — critic + lint + filing-back (다음)

**자가학습 + 영구 누적**.

- ☐ `backend/agents/critic.py` 본격 — citation 검증 + patch flag + JSON output
- ☐ writer → critic sequential pipe
- ☐ `backend/jobs/wiki_lint.py` — 주기 점검 (모순 / stale / orphan / 누락 cross-ref)
- ☐ `/ask` 답변을 wiki 페이지로 filing-back (사용자 명시 trigger)
- ☐ Phase 4 학습 데이터 export 후크 (`backend/jobs/export_wiki_training.py` 자리만)

### D10.5 (wave-1+2 안정화 후, 우선순위 높음)

- ☐ `frontend/components/ItemDetails.tsx` 에 user_notes append textarea — 모든 viewer 공통 (graph item / wiki source / cleanup)
- 백엔드 `POST /items/{id}/notes` 이미 동작 — frontend 통합만 (반나절)

### D12-4 (D10 wave-2 후)

- ☐ cleanup 페이지 재정의: placeholder viewer + wiki classifier 결과 활용 (자료 부족 wiki page filter + 보강 가치 마킹)

### D10.5 wave — ItemDetails user_notes 통합 (반나절)

- ☐ `frontend/components/ItemDetails.tsx` 에 user_notes append textarea
- ☐ 모든 viewer (graph item / wiki source / cleanup) 공통 1급 기능

### D12-4 (D10 안정화 후)

- ☐ cleanup 페이지 재정의: placeholder viewer + classifier 결과 확인 + "보강 가치 높음" filter

---

## 10.5 external repo 패턴 차용 (2026-05-26 사용자 제안)

사용자가 명시 — `external/ml-intern` + `external/physics-intern` 의 agent 패턴 참고.

### ml-intern (Apache 2.0 ✓ vendor 가능)

| 패턴 | LinkMind 적용 |
|---|---|
| **Research sub-agent (context 분리)** | `retriever` / `classifier` 는 sub-agent (작은 context, 작은 LLM 호출) → main `writer` 의 context 오염 X |
| **YAML prompt 템플릿 + 버전 관리** | `backend/agents/prompts/*.yaml` 시드 → `prompts` DB 테이블로 import. UI Settings 탭에서 편집 가능 (기존 패턴 유지) |
| LiteLLM provider 추상화 | LinkMind 는 이미 자체 `backend/llm/factory.py` 있음 → 차용 불필요 |
| Queue 기반 비동기 | LinkMind 도 FastAPI async + BackgroundTask 패턴 — 동일 |

### physics-intern (LICENSE 파일 없음 — **코드 vendor X, 패턴 차용만**)

| 패턴 | LinkMind 적용 |
|---|---|
| **State-centric architecture** ⭐⭐⭐ | `wiki_pages` row 가 single source of truth. agent 간 conversation history X. **매 agent call 은 fresh context** 를 `wiki_pages` + 연관 `items` + `item_topics` 에서 build. async/resumable 친화 — LinkMind 의 분산 BackgroundTask 와 정합. |
| **BaseAgent ABC + template method** | `backend/agents/base.py` 에 abstract: `build_context()` + `run()` + `parse_output()`. 4 agent 다 동일 inheritance |
| **Structured JSON output + parse-failure retry** | classifier / critic 의 output 은 JSON (page_id 매칭 / critique 등). parse 실패 시 같은 conversation 안에서 재시도 → 안 되면 fresh call |
| **build_context() XML 섹션 조립** | prompt 안에 `<wiki_page>...`</wiki_page>` `<sources>...</sources>` 같은 XML 섹션 — LLM 이 context 경계 인지 |

### 적용 우선순위

| # | 패턴 | 어디 |
|---|---|---|
| 1 | State-centric (physics-intern) | `backend/agents/base.py` 의 핵심 설계 원칙 |
| 2 | BaseAgent ABC (physics-intern) | `backend/agents/base.py` 구조 |
| 3 | YAML prompt + version (ml-intern) | `backend/agents/prompts/*.yaml` |
| 4 | Sub-agent context 분리 (ml-intern) | classifier / retriever 가 작은 LLM, 짧은 context |
| 5 | Structured JSON output (physics-intern) | classifier / critic output 형식 |

### 라이센스 attribution (vendor 시 §11 준수)

- ml-intern 패턴 코드 일부 차용 시 — `# Adapted from ml-intern/<file> (Apache-2.0) — Copyright (c) 2025 ...` 주석
- physics-intern — **코드 vendor X**, idea/패턴만 (license 없음 → all rights reserved)

---

## 11. 결정 사항 요약 (2026-05-26 사용자 결정 반영)

| # | 결정 | 채택 | 상태 |
|---|---|---|---|
| 1 | wiki 본문 저장 형태 | **B. 별 wiki_pages 테이블 + wiki_page_versions** (확장성 + 버전 히스토리 + 학습 신호) | ✅ 확정 |
| 2 | wiki body 갱신 시점 | **C → A** (wave-1 lazy → 안정화 후 자동) | ✅ 확정 |
| 3 | agent orchestration | **A. 자체 sequential** (§6 MVP, framework 없음) + physics-intern state-centric 패턴 | ✅ 확정 (추천대로) |
| 4 | classifier 와 categories 관계 | **A→C 단계적** (wave-1 공존, wave-2 후 점진 대체) | ✅ 확정 (추천대로) |
| 5 | 첫 prototype scope | **B 의 단계적 변형** — wave-1 에 4 agent 골격 + 검색 layer 전환 (사용자 "제대로" + "검색 fix" 요구 반영) | ✅ 확정 |
| 6 | classifier 호출 시점 | wave-2 — ingest 직후 BackgroundTask + POST `/wiki/classify` 수동 backfill | ✅ 확정 |
| 7 | external repo 패턴 | physics-intern state-centric + BaseAgent ABC, ml-intern YAML prompt + sub-agent context 분리 | ✅ 확정 (사용자 제안) |

**모든 결정 확정 시 D10 wave-1 코드 진입.**

---

## 12. 참조

- karpathy llm_wiki 원문: `external/karpathy/llm_wiki` (MIT, gitignored)
- memory: `project-llm-wiki-arch`, `project-next-session-entrypoint`
- CLAUDE.md: §1 학습 비전, §3 책임 분리, §11 NEVER, §14 Privacy
- 관련 기존 코드:
  - [backend/api/ask.py](backend/api/ask.py) — 현재 RAG 흐름
  - [backend/api/search.py](backend/api/search.py) — Qdrant 검색
  - [backend/db/schema.sql](backend/db/schema.sql) — topics / categories / items_topics
  - [backend/jobs/analysis_worker.py](backend/jobs/analysis_worker.py) — BackgroundTask 패턴
  - [backend/jobs/auto_link_categories.py](backend/jobs/auto_link_categories.py) — 휴리스틱 categories
