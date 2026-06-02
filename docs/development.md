# 개발 가이드 (Development)

LinkMind 개발 환경·코드 스타일·테스트·디렉토리 구조 정리. 전체 개요는 [README](../README.ko.md),
아키텍처 상세는 [agent_architecture.md](agent_architecture.md), wiki 설계는
[llm_wiki_design.md](llm_wiki_design.md), 기능 백로그는 [features_backlog.md](features_backlog.md) 참고.

---

## 1. 기술 스택 / 환경

- **OS**: Ubuntu, **GPU**: NVIDIA RTX 4090 (CUDA 24GB), **Docker**: nvidia-container-toolkit
- **Backend**: Python 3.11+ (검증: 3.13.12 + torch 2.6.0+cu124, NVIDIA driver 580.x), FastAPI,
  SQLAlchemy 2.0 async + asyncpg, pydantic-settings
- **DB**: PostgreSQL 16 (관계형 + raw 본문) + Qdrant 1.12 (벡터)
- **Embedding**: sentence-transformers (bge-m3, 1024 dim) — vLLM-embed 컨테이너로 HTTP 공유
- **LLM**: vLLM (Gemma 4 26B-A4B-AWQ, 기본) / OpenAI / Anthropic / Ollama (provider abstraction).
  모델·구동 설정은 DB(`app_settings`) + Settings UI 에서 관리, `scripts/vllm_restart.sh` 로 재구동
- **Frontend**: Next.js 16 App Router + TypeScript + Tailwind v4 + react-force-graph-3d + three.js
- **Object storage**: 로컬 FS (→ MinIO 예정)
- **Python 환경**: **venv** (conda 아님). 시스템 의존성은 Docker 가 격리하고 Python 패키지는 표준 pip.

설정은 **모두 `env/dev.env` 환경변수**로 관리한다. 코드에 비밀값/하드코딩 금지.
`os.environ` 직접 접근 금지 — 항상 `backend.config.get_settings()` 경유.

---

## 2. 데이터 5대 원칙 (절대 위반 금지)

분석 결과(summary, embedding)는 재생성 가능하지만 raw 가 깨지면 복구 불가. **항상 raw 를 먼저 저장하고 분석은 그 후.**

| 원칙 | 의미 | 강제 위치 |
|---|---|---|
| Raw-first | 원본 텍스트/파일 무손실 보존 | `items.raw_content NOT NULL` |
| Provenance | source_type/source_url/source_id/hash 추적 | schema NOT NULL 제약 |
| Idempotent | 동일 자료 중복 저장 금지 | `UNIQUE(source_type, raw_content_hash)` |
| Versioned analysis | 요약/임베딩에 model 버전 기록 | `summary_model`, `embedding_model` 컬럼 |
| Loss-less storage | 이미지/PDF resize/compress 금지 | `attachments.file_hash` 그대로 |

---

## 3. 코드 스타일

- **Python typing 필수**, `from __future__ import annotations`
- **async/await 우선** (블로킹 호출은 `asyncio.to_thread`)
- **Pydantic schema** 로 외부 인터페이스 정의 (`backend/schemas/`)
- **FastAPI router 구조** (`backend/api/<feature>.py`)
- **함수 단위 분리 + 서비스 단위 모듈화**, 지나친 OOP 지양
- 주석은 한국어 OK, 충분히 (특히 "왜 이렇게 했는지")
- 변수/함수 이름은 영어 + snake_case

**MVP 원칙**: 동작하는 MVP > Clean Architecture. 과한 추상화/디자인 패턴/미래 가정 기능 지양.
단 재배포·서버 이전·온프레미스 설치 가능 구조(env, 볼륨, compose, healthcheck 분리)는 처음부터 유지.

---

## 4. Git / Commit 규칙

- **모든 commit 메시지는 한국어**. 영문 conventional prefix(`feat:`/`fix:`/`chore:`) 사용 금지.
  - 예: `"초기 scaffold: ..."`, `"수정: ..."`, `"리팩토링: ..."`
  - 코드 식별자, 명령어, 외부 시스템명(Postgres, Qdrant 등)은 원문 유지
- `git push --force`, `git reset --hard` 는 명시적 지시 없으면 금지
- `.env`, `volumes/`, `archive/`, `__pycache__/` 등은 commit 금지 (.gitignore 처리)

---

## 5. 자주 쓰는 명령어

셋업 스크립트는 `stepN_setup_*` / `stepN_check_*` 쌍 패턴. 각 step 직후 같은 번호의 check 로 sanity 확인.

```bash
# step1: Python 베이스 환경 (.venv + torch cu124 + requirements)
bash scripts/step1_install_base_env.sh
source .venv/bin/activate
bash scripts/step1_check_base_env.sh

# step2_1: 호스트에 Docker + NVIDIA Container Toolkit (sudo, 한 번만)
bash scripts/step2_1_install_docker.sh
bash scripts/step2_1_check_docker.sh        # 새 셸 또는 'newgrp docker' 후

# step2_2: 인프라 (Postgres + Qdrant 등)
bash scripts/step2_2_setup_infra.sh
bash scripts/step2_2_check_infra.sh

# step4: Qdrant 컬렉션 (bge-m3 1.4GB 첫 다운로드)
python -m backend.jobs.init_qdrant
bash scripts/step4_check_qdrant.sh

# vLLM (Gemma 4, 기본 LLM) + vLLM-embed (bge-m3)
docker compose --env-file env/dev.env -f compose/docker-compose.dev.yml --profile vllm up -d

# (선택) Ollama provider — vLLM 대신 쓸 때만
bash scripts/step3_setup_ollama.sh
bash scripts/step3_check_ollama.sh

# step5: backend(:8000) + frontend(:3001) + telegram watcher 한 번에
bash scripts/step5_run_dev.sh                # --stop / --status / --foreground / --skip-check

# URL 하나 수동 수집
python -m backend.ingest.url https://arxiv.org/abs/2401.01234

# 텔레그램 inbox watcher
python -m ai_agents.telegram_inbox_watcher                # 자동 backfill → listen (기본)
python -m ai_agents.telegram_inbox_watcher --no-backfill  # listen 만
python -m ai_agents.telegram_inbox_watcher --backfill 50 --no-listen  # 지난 50개 일괄

# 위키 상태 / backfill / 정리 (모두 idempotent — dry-run 먼저)
curl -s http://localhost:8000/wiki/_meta/stats | jq
bash scripts/run_wiki_backfill.sh            # 옛 page 일괄 wiki body 합성 (--status/--concurrency N)
python -m backend.jobs.cleanup_duplicate_wikis --dry-run
python -m backend.jobs.normalize_keywords --dry-run
python -m backend.jobs.link_photo_captions --dry-run

# vLLM 모델/설정 관리 (DB 일원화)
bash scripts/vllm_restart.sh --dry-run       # DB(app_settings)의 vLLM 설정 확인
bash scripts/vllm_restart.sh                 # DB 값으로 컨테이너 재구동
```

---

## 6. 테스트 정책 — 새 함수 추가 시 동반 작성 필수

새 기능/함수를 추가하거나 기존 함수 동작이 바뀌면 같은 PR/commit 안에서 단위 테스트도 함께
작성/갱신한다 (회귀 방지 + CI 보장).

### 카테고리 5종 (마커)

| 마커 | 위치 | 어디서 | 비고 |
|---|---|---|---|
| `cpu` (마커 없음) | `tests/*.py` | CI + 로컬 (≈4s) | pure unit + mock + fixture |
| `embedding` | `tests/embedding/` | CI + 로컬 | 가벼운 MiniLM-L6-v2 (~80MB), CPU |
| `integration` | `tests/integration/` | backend live (로컬), CI skip | FastAPI e2e — 미가동 시 pytest.skip |
| `llm` | `tests/llm/` | Ollama live (로컬), CI skip | 실 LLM 호출 sanity |
| `gpu` | `tests/gpu/` | 로컬(RTX 4090) 전용 | CUDA 강제, CI 자동 deselect |

### 결정 흐름

1. **pure 함수** (DB/네트워크 없음) → `tests/`, 마커 없음
2. **외부 모듈 호출** (httpx, yt_dlp, GitHub API …) → monkeypatch mock
3. **DB/Postgres 필요** → `tests/integration/` + `@pytest.mark.integration`
4. **sentence-transformers/임베딩** → `tests/embedding/` + `@pytest.mark.embedding` (가벼운 MiniLM)
5. **CUDA 강제** → `tests/gpu/` + `@pytest.mark.gpu`
6. **실 LLM 호출** → `tests/llm/` + `@pytest.mark.llm` (Ollama health 체크 + 짧은 호출)

### 명령

```bash
bash scripts/tests/total/run_all_local.sh       # 5 카테고리 다 (로컬)
bash scripts/tests/total/run_ci_simulation.sh   # CI 가 도는 것만 (push 전 점검)
.venv/bin/pytest -m '' tests/                    # marker 무시 전체
.venv/bin/pytest -m embedding tests/             # 단일 카테고리
```

CI(`.github/workflows/ci.yml`)는 `pytest -m "not gpu"` — GPU 만 deselect, 나머지는 환경 미충족 시 fixture skip.

---

## 7. 디렉토리 구조

```
backend/
├─ api/         # FastAPI routers (health, ingest, search, ask, graph, settings, files, wiki ...)
├─ config.py    # pydantic-settings 환경설정 (모든 env 진입점)
├─ db/          # connection.py, repository.py, schema.sql
├─ embedding/   # base.py, local.py, vllm_embed.py, factory.py, qdrant_store.py, wiki_qdrant.py
├─ llm/         # base.py, factory.py, openai/claude/ollama provider
├─ ingest/      # source 별 (url/ slack/ telegram/ pdf/ github/ arxiv/ youtube/ document/) + auto dispatcher
├─ agents/      # wiki agents — classifier / retriever / writer / (critic) + prompts/
├─ schemas/     # Pydantic 요청·응답 모델
├─ storage/     # local.py (raw 파일 무손실 보존)
├─ utils/       # chunking, hashing, external_ids, keywords, wiki_slug
├─ jobs/        # batch (init_db / init_qdrant / backfill_* / cleanup_* / generate_*) — `python -m backend.jobs.<name>`
└─ main.py      # FastAPI 진입점 (lifespan: analysis_worker + wiki_writer_worker daemon)

ai_agents/                 # multi-channel inbox/gateway daemon (backend HTTP API 호출, LLM 직접 호출 금지)
├─ base.py                 #   ChannelAgent ABC (setup / listen / on_message → ingest)
├─ telegram_inbox_watcher.py
├─ check_telegram_invites.py
└─ (slack/whatsapp/discord — 예정)

frontend/                  # Next.js 16 + React 19 + Tailwind v4
├─ app/                    #   App Router pages: /ask /wiki /wiki/[slug] /ingest /settings
├─ components/             #   GraphView, WikiDetailView, KeywordsEditor, wiki/*
├─ lib/                    #   api.ts, askStore.ts, i18n, colors
└─ types/                  #   TypeScript schemas

compose/docker-compose.*   # docker compose 정의
env/                       # dev.env(gitignored) / dev.env.example
scripts/                   # 실행용 .sh (stepN_*.sh / vllm_restart.sh / run_wiki_backfill.sh / tests/)
docs/                      # 설계·셋업 문서
archive/ · volumes/        # raw 자료 · 컨테이너 영속 볼륨 (둘 다 gitignored)
```

---

## 8. 외부 코드 vendor 시 라이센스

`external/{...}/` 의 참조 clone 은 **벤치마킹 참조 전용**(gitignored). 일부 코드를 vendor 하려면:

- license 호환 확인 (MIT/Apache/GPL → AGPL 호환, BSL/proprietary 금지)
- MIT/Apache vendor 시 **LICENSE 파일 + copyright notice + 출처 주석** 필수
  (예: `# Adapted from <repo>/<file> (MIT) — Copyright (c) ...`)
- 함수명/변수명만 바꿔 license 우회 금지 (법적 derivative work)
- vendor 한 코드는 repo 안에 복사 — `external/` 경로로 import 금지
