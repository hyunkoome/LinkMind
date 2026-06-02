<div align="center">

# LinkMind

**텔레그램·URL 로 모은 자료를 raw-first 로 보존하고, llm_wiki 로 자동 합성해 대화형으로 묻는 self-contained personal AI engine**

[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![Next.js](https://img.shields.io/badge/Next.js-16-000000?logo=nextdotjs&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)
![Qdrant](https://img.shields.io/badge/Qdrant-1.12-DC244C)
![vLLM](https://img.shields.io/badge/vLLM-Gemma%204%2026B--A4B-FFCE00)

[English](README.md) | **한국어**

</div>

---

## 소개

**LinkMind 는 흩어진 자료를 한곳에 모아 "내 지식"으로 만드는 온프레미스 AI 엔진**이다. 텔레그램에 링크를 던지거나 URL 을 넣으면, 원본을 무손실로 보존하고(AI 요약 + 임베딩), 같은 주제끼리 묶어 **llm_wiki** 위키 페이지로 자동 합성한 뒤, 대화형 RAG(`/ask`)로 질문할 수 있다.

backend · agent · UI 를 한 저장소에서 같이 유지하는 **단일 self-contained 시스템**이라, 외부 client agent 없이 self-host 한 방에 다 따라온다.

그런데 LinkMind 자체는 **수단**이다. 진짜 목표는 사용자가 누적한 데이터로 **sVLL(small Vision-Language LLM)을 LoRA 파인튜닝**해서 본인만의 personalized AI 엔진을 만들고, 이를 지속적으로 재학습(continuous training loop)하는 것이다. 그래서 모든 설계 결정은 **"이게 학습 데이터를 보존/구조화/내보내는 데 도움이 되는가?"** 라는 질문을 통과한다.

> **배포 전략**: self-host 우선·기본 모드. 장기적으로 OSS(AGPL v3) 공개 + hosted SaaS 옵션. 단 **사용자 데이터로 운영자의 공통 모델을 학습하는 것은 절대 금지** — personal LoRA 는 "본인 데이터로 본인 모델만".

---

## ✨ 주요 기능

<details open>
<summary><b>📥 수집 (ingest) — raw-first 무손실 보존</b></summary>

<br>

- **다양한 소스**: URL / PDF / DOCX / PPTX / TXT / MD / GitHub / arxiv / YouTube(영상·playlist·channel) / 이미지 — host 자동 라우팅 dispatcher.
- **raw-first**: 원본 무손실 저장 + provenance(출처) 추적 + idempotent(UNIQUE hash 로 중복 차단) + 첨부 SHA-256 dedup 영구 보존.
- **fallback 안전망**: 본문 추출에 실패해도 raw + URL 은 항상 보존 (OG meta / YouTube oEmbed fallback, `fetch_error` 마킹).
- **arxiv / IEEE / DOI URL** 에서 진짜 논문 제목 추출. 같은 주제(arxiv_id / github_repo / doi / yt_id)는 자동으로 한 topic 으로 묶임.
- **텔레그램 multi-channel inbox watcher**: yaml 단일 진실, FloodWait/cache 처리, 처리 성공 시 메시지 삭제.
- **Slack export 일회성 backfill**: thread / mrkdwn / 첨부 파서.
- **AI 요약**(한국어 bullet, Gemma 4) + **임베딩**(bge-m3 1024 dim, vLLM-embed HTTP 공유).

</details>

<details>
<summary><b>📚 위키 (llm_wiki) — LinkMind 의 정체성</b></summary>

<br>

일반 chunk-RAG 가 아니라 **karpathy llm_wiki + multi-agent** 패턴.

- **4 agent**: `classifier`(자료→wiki 매핑) / `retriever` / `writer`(5섹션 markdown 합성) / `critic`(stub).
- **자동 흐름**: 신규 ingest → `analysis_worker`(summary) → `classifier`(매핑 + pending) → `wiki_writer_worker` daemon(자동 합성).
- **1 링크 = 1 위키**: confidence ≥ 0.9 자기 정체성 topic 만 primary 위키, `native_identity_external_id` 보장. cross-modal 단서(0.7)는 link 만.
- **키워드**: 정규화(영문만 + camelCase + 약어/별칭, 예 `LiDAR→lidar`, `3D Gaussian Splatting→3dgs`) + 클라우드 사이드바(빈도순 + ⭐ + 다중 AND filter). Settings + DB 편집.
- **사진**: '사진 + URL' = 본문 figure link, standalone 사진 위키 정리(raw 보존). 사진만 있는 메시지는 ingest 안 함.
- **status 3종**: `issues`(잔여/실패) / `pending`(처리 큐) / `completed`(클릭 가능). sub-state `body_processing_started_at` 로 generating / queuing 구분.
- **도구**: backfill(concurrency 4) + 정리 job(`cleanup_duplicate_wikis` / `normalize_keywords` / `link_photo_captions`).

</details>

<details>
<summary><b>💬 대화형 RAG (/ask)</b></summary>

<br>

- 홈(`/`)이 `/ask` 로 redirect — LinkMind 의 메인 경로.
- 2-column UI: 좌(질문 입력 + 답변 + citation chips + related_wikis) / 우(클릭한 wiki detail inline view).
- 답변은 **반드시 DB 자료 기반** RAG — 일반 LLM 응답이 아님.
- 현재 1-shot RAG(Step 1). 다음 단계로 멀티턴 + 검색 + agentic action 확장 예정.

</details>

<details>
<summary><b>🖥️ UI (Next.js 16 + React 19)</b></summary>

<br>

- `/ask` — 대화형 RAG (메인·홈)
- `/wiki` — 리스트(키워드 cloud + status/정렬/검색 필터 + 4 tab + pagination + 페이지당 라디오 + ETA + 7필드 검색) + 우측 inline 상세 패널(`WikiDetailView`: 편집/재합성/Sources/Relationship/Keywords/메타/삭제)
- `/wiki/[slug]` — 단독 상세 페이지(`WikiDetailView` 공용)
- `/ingest` · `/settings`(키워드 약어·별칭 편집)
- i18n(한/EN) + ThemeToggle(☀️/🌙/🖥) + 자료/위키 삭제(2단계 confirm, Qdrant + Postgres CASCADE, **raw 보존**)
- `/graph`(keyword 관계 그래프)는 보류 — 메인 nav 제거, URL 직접 접근만 (코드·endpoint 는 남김).

</details>

---

## 🧩 아키텍처

backend + agent + UI 를 한 저장소에서 유지하는 단일 배포 단위. 모든 모듈이 같은 venv · 같은 Postgres · 같은 Qdrant 를 공유한다.

```
┌─────────────────────────────────────────────────────────────┐
│                        frontend/ (Next.js 16)                │
│   /ask · /wiki · /wiki/[slug] · /ingest · /settings  :3001   │
└───────────────────────────────┬─────────────────────────────┘
                                 │ HTTP
┌───────────────────────────────▼─────────────────────────────┐
│                     backend/ (FastAPI)  :8000                │
│  api: /ingest /search /ask /wiki /graph /items /categories   │
│       /files /settings /health   (/docs Swagger)             │
│  ingest dispatcher · 4 wiki agents · analysis_worker         │
│  wiki_writer_worker daemon · LLM provider abstraction        │
└──────┬─────────────────────┬──────────────────┬─────────────┘
       │                     │                  │
┌──────▼──────┐      ┌───────▼──────┐    ┌──────▼──────────────┐
│ PostgreSQL  │      │   Qdrant     │    │  vLLM (Gemma 4)     │
│ 16          │      │   1.12       │    │  vLLM-embed (bge-m3)│
│ raw + 관계  │      │   벡터 검색  │    │  :8002 /v1/embed... │
└─────────────┘      └──────────────┘    └─────────────────────┘
       ▲
┌──────┴──────────────────────────────────────────────────────┐
│         ai_agents/  — multi-channel inbox watcher            │
│   telegram_inbox_watcher (현재) → slack/whatsapp/discord     │
│   ※ LLM 은 직접 호출 안 함, backend HTTP /ask 경유            │
└──────────────────────────────────────────────────────────────┘
```

| 모듈 | 역할 |
|---|---|
| **`backend/`** | FastAPI HTTP API, DB, embedding, LLM provider, ingest 모듈, wiki agent, worker daemon |
| **`ai_agents/`** | 여러 채널의 inbox/gateway daemon. backend HTTP API 호출 (LLM 직접 호출 금지) |
| **`frontend/`** | Next.js 16 App Router + React 19 + Tailwind v4 + react-force-graph-3d + three.js |

**기술 스택**: Python 3.11+ (검증 3.13.12 + torch 2.6.0+cu124) · FastAPI · SQLAlchemy 2.0 async + asyncpg · pydantic-settings · PostgreSQL 16 · Qdrant 1.12 · vLLM(Gemma 4 26B-A4B MoE-AWQ, KV cache fp8 + 16384 context) · sentence-transformers(bge-m3) · NVIDIA RTX 4090(CUDA 24GB) · Docker + nvidia-container-toolkit.

> **LLM 은 vLLM(Gemma 4)이 기본.** OpenAI / Anthropic / Ollama 도 provider 추상화로 지원하지만 선택 사항이다. 모델·구동 설정은 DB(`app_settings`) + Settings UI 에서 관리하고 `scripts/vllm_restart.sh` 로 재구동한다.

---

## 🚀 빠른 시작

> **사전 요건**: Ubuntu(또는 WSL2), NVIDIA RTX 4090(CUDA 24GB) 권장, Docker 24+ + nvidia-container-toolkit. 모든 설정은 `env/dev.env` 환경변수로 관리한다(`env/dev.env.example` 복사).

<details open>
<summary><b>step1 — Python 베이스 환경</b></summary>

```bash
bash scripts/step1_install_base_env.sh        # .venv + torch cu124 + requirements
source .venv/bin/activate
bash scripts/step1_check_base_env.sh
```

</details>

<details>
<summary><b>step2 — Docker + 인프라 (Postgres + Qdrant)</b></summary>

```bash
# step2_1: 호스트에 Docker + NVIDIA Container Toolkit 설치 (sudo, 한 번만)
bash scripts/step2_1_install_docker.sh
bash scripts/step2_1_check_docker.sh          # 새 셸 또는 'newgrp docker' 후

# step2_2: LinkMind 인프라 기동
bash scripts/step2_2_setup_infra.sh
bash scripts/step2_2_check_infra.sh
```

</details>

<details>
<summary><b>step3 — Qdrant 컬렉션 + vLLM (기본 LLM)</b></summary>

```bash
# Qdrant 컬렉션 (bge-m3 1.4GB 첫 다운로드)
python -m backend.jobs.init_qdrant
bash scripts/step4_check_qdrant.sh

# vLLM (Gemma 4 26B-A4B-AWQ, 기본 LLM) + vLLM-embed (bge-m3) 컨테이너
docker compose --env-file env/dev.env -f compose/docker-compose.dev.yml \
    --profile vllm up -d
# 모델/구동 파라미터는 DB(app_settings) + Settings UI 에서 관리, scripts/vllm_restart.sh 로 재구동
```

</details>

<details>
<summary><b>(선택) Ollama provider — vLLM 대신 쓸 때만</b></summary>

```bash
# 기본 LLM 은 vLLM(Gemma 4). Ollama 는 provider 추상화의 선택적 fallback 일 뿐 —
# vLLM 으로 충분하면 이 단계는 건너뛴다.
bash scripts/step3_setup_ollama.sh            # env 의 OLLAMA_MODEL pull
bash scripts/step3_check_ollama.sh
```

</details>

<details open>
<summary><b>step5 — 전체 가동 (backend + frontend + telegram watcher)</b></summary>

```bash
bash scripts/step5_run_dev.sh                 # 셋 다 한 번에 (권장)
bash scripts/step5_run_dev.sh --status        # 상태 확인
bash scripts/step5_run_dev.sh --stop          # 정지
```

</details>

가동 후 접속:

| 서비스 | URL |
|---|---|
| Frontend (Next.js) | http://localhost:3001 |
| Backend API (Swagger) | http://localhost:8000/docs |
| vLLM-embed (bge-m3) | http://localhost:8002/v1/embeddings |

---

## 💡 사용법

**URL 하나 수동 수집**

```bash
python -m backend.ingest.url https://arxiv.org/abs/2401.01234
```

**텔레그램 inbox watcher** — 채널에 링크를 던지면 자동으로 ingest → 요약 → wiki 합성

```bash
python -m ai_agents.telegram_inbox_watcher                # 자동 backfill → listen (기본)
python -m ai_agents.telegram_inbox_watcher --no-backfill  # backfill 없이 listen 만
python -m ai_agents.telegram_inbox_watcher --backfill 50 --no-listen  # 지난 50개만 일괄 처리
```

> step5 (`bash scripts/step5_run_dev.sh`)가 backend·frontend 와 함께 이 watcher 를 자동으로 백그라운드 기동한다.

**대화형 질문 (`/ask`)** — http://localhost:3001/ask 에서 자연어로 질문. 답변은 누적된 DB 자료 기반 RAG 로 생성되고, citation chips · related_wikis 가 함께 표시된다. 위키 카드를 클릭하면 우측 패널에 상세가 inline 으로 열린다.

**위키 상태 확인**

```bash
curl -s http://localhost:8000/wiki/_meta/stats | jq      # completed / pending / issues
```

**위키 backfill / 정리 (사용자 직접, 모두 idempotent — dry-run 먼저)**

```bash
bash scripts/run_wiki_backfill.sh                          # 옛 page 일괄 wiki body 합성 (concurrency 4)
bash scripts/run_wiki_backfill.sh --status                 # 진행률 요약
python -m backend.jobs.cleanup_duplicate_wikis --dry-run   # 중복 위키 정리 미리보기
python -m backend.jobs.normalize_keywords --dry-run        # 키워드 정규화 미리보기
python -m backend.jobs.link_photo_captions --dry-run       # 사진 figure 연결 미리보기
```

---

## 🔒 데이터 5대 원칙

분석 결과(summary, embedding)는 재생성 가능하지만 raw 가 깨지면 복구 불가다. **항상 raw 를 먼저 저장하고 분석은 그 후.**

| 원칙 | 의미 | 강제 위치 |
|---|---|---|
| **Raw-first** | 원본 텍스트/파일 무손실 보존 | `items.raw_content NOT NULL` |
| **Provenance** | source_type / source_url / source_id / hash 추적 | schema `NOT NULL` 제약 |
| **Idempotent** | 동일 자료 중복 저장 금지 | `UNIQUE(source_type, raw_content_hash)` |
| **Versioned analysis** | 요약/임베딩에 model 버전 기록 | `summary_model`, `embedding_model` 컬럼 |
| **Loss-less storage** | 이미지/PDF resize·compress 금지 | `attachments.file_hash` 그대로 |

---

## 🗺️ 로드맵

| Phase | 상태 | 핵심 |
|---|---|---|
| **1** | ✅ 완료 | Postgres + Qdrant + URL ingest + Embedding + Semantic Search + RAG |
| **2** | ✅ 대부분 완료 | AI 요약/태깅, Slack export 파서, 임베딩 인프라(vLLM-embed), 카테고리 강화. 남은 것: feedback 테이블 ⏳, dataset exporter ⏳ |
| **2.5** | ✅ 완료 | Topic 그래프, ChannelAgent ABC, Next.js 16 + react-force-graph-3d UI, modality-aware viewer, 3-tier categories, Telegram multi-channel |
| **3 (D10 wiki)** | ✅ wave-1+2 | schema + 4 agent + wiki API + Qdrant body search + frontend wiki list/detail + KeywordsEditor + backfill + writer daemon + batch CLI |
| **3 (D11 UX 통합)** | ✅ 완료 | wiki 중심 통합, status 모델 통일, `/ask` 신규 페이지(Step 1), 검색 7 필드 확장 |
| **3 (D10.6)** | ✅ 완료 | "1 링크 = 1 위키", 키워드 정규화/클라우드, 사진 figure 연결 |
| **3 (남은)** | 🚧 진행 | 대화형 `/ask`(멀티턴 + 검색 + agentic action), ai_agents 실제 채널 확장, OCR/멀티모달, 자가학습, critic agent, dataset exporter |
| **4** | ⬜ 미시작 | **sVLL LoRA 파인튜닝** (Gemma 4 26B-A4B QLoRA 또는 Qwen2-VL), vLLM 서빙 |
| **5** | ⬜ 미시작 | Continuous training loop, 온프레미스 AI 엔진 완성 |
| **6 (선택)** | 구상 | OSS(AGPL v3) 공개 → hosted SaaS (Auth.js + Stripe, multi-tenant, BYOK) |

> 다음 우선순위: **대화형 `/ask`(멀티턴 + 검색 + agentic action)** → **학습 파이프라인(Phase 4)**.

---

## 🤝 기여

LinkMind 는 현재 **단독 개발 단계**(self-host 완성도에 집중)다. 아직 외부 contribution 워크플로를 공식화하지 않았다. 버그 리포트·기능 제안·질문은 [GitHub Issues](https://github.com/hyunkoome/LinkMind/issues)로 남겨주면 검토한다.

참고로 이 저장소의 모든 문서·커밋 메시지는 한국어로 작성되며, 새 기능 추가 시 단위 테스트 동반 작성이 원칙이다.

---

## 📜 라이센스

LinkMind 는 두 가지 라이센스 옵션을 지향한다 (현재는 self-host 단계로, 상용 라이센스는 "문의 기반"이다).

- **AGPL-3.0** — 연구 · self-host · 개인/회사 내부 사용은 무제한 자유. 단 변형해서 네트워크 서비스(SaaS)로 제공할 경우 변경 소스 공개 의무가 따른다 (Plausible / Cal.com / n8n 채택 모델). 자세한 조건은 [`LICENSE`](LICENSE) 참고.
- **상용 라이센스** — AGPL-3.0 의 공개 의무 없이 닫힌 제품이나 SaaS 에 통합하고 싶다면 이메일로 문의: **hyunkookim.me@gmail.com**

Copyright (C) 2026 Hyunkoo Kim ([@hyunkoome](https://github.com/hyunkoome)).

---

## 📞 문의

- **GitHub Issues** — https://github.com/hyunkoome/LinkMind/issues
- **Email** — hyunkookim.me@gmail.com

<div align="center">

**LinkMind** — 내가 모은 자료가, 결국 나만의 AI 가 된다.

</div>
