# CLAUDE.md

이 파일은 LinkMind 저장소에서 Claude Code 가 작업할 때 자동 로드되는 가이드라인이다. 매 세션마다 같은 컨텍스트를 반복 설명할 필요 없도록 핵심만 압축해서 둔다.

> 자세한 내용은 `README.md`, `docs/agent_architecture.md`, `docs/training_data_design.md` 참고.

---

## 1. 프로젝트의 진짜 목표

**LinkMind 자체는 수단**이다. 최종 목표는 **사용자가 누적한 데이터로 sVLL(small Vision-Language LLM)을 LoRA 파인튜닝해서 온프레미스 personalized AI 엔진**을 만드는 것. 그 엔진을 **지속적으로 재학습**(continuous training loop)하는 게 장기 비전.

→ LinkMind 의 모든 설계 결정은 "이게 학습 데이터를 보존/구조화/내보내는 데 도움이 되는가?" 라는 질문을 통과해야 한다.

**배포 전략**: self-host 가 우선이고 기본 사용 모드. 장기적으로 OSS (AGPL v3) 공개 + hosted SaaS 옵션 (§14 참조). 단, **사용자 데이터로 운영자의 공통 모델을 학습하는 것은 절대 금지** — personal LoRA 는 "사용자 본인 데이터로 본인 모델만" 이 유일한 형태.

## 2. 데이터 5대 원칙 (절대 위반 금지)

| 원칙 | 의미 | 강제 위치 |
|---|---|---|
| Raw-first | 원본 텍스트/파일 무손실 보존 | `items.raw_content NOT NULL` |
| Provenance | source_type/source_url/source_id/hash 추적 | schema NOT NULL 제약 |
| Idempotent | 동일 자료 중복 저장 금지 | `UNIQUE(source_type, raw_content_hash)` |
| Versioned analysis | 요약/태깅에 model/prompt 버전 기록 | `summary_model`, `embedding_model` 컬럼 |
| Loss-less storage | 이미지/PDF resize/compress 금지 | `attachments.file_hash` 그대로 |

분석 결과(summary, embedding) 는 재생성 가능하지만 raw 가 깨지면 복구 불가. **항상 raw 를 먼저 저장하고 분석은 그 후.**

## 3. 시스템 아키텍처: 단일 self-contained 시스템

LinkMind 는 **self-contained personal AI engine** — backend + agent + UI 를 한 저장소에서 같이 유지. 외부 client agent (openclaw / hermes-agent 등) 에 의존하지 않는다. self-host 한 방에 다 따라온다.

### 모듈 구조

- **`backend/`** — HTTP API (`/ingest`, `/search`, `/ask`, `/graph`, `/items`, `/categories`, `/files`, `/settings`), DB, embedding, LLM provider, ingest 모듈
- **`ai_agents/`** — 여러 채널의 inbox/gateway daemon. backend HTTP API 호출. ChannelAgent ABC 로 추상화 (wave-3+)
  - 현재: `telegram_inbox_watcher` (multi-channel via yaml, wave-5 부터 sequential)
  - 단계적 확장 (Phase 3+, 사용자 채널 사용 빈도에 따라): slack, whatsapp, discord 등
- **`frontend/`** — 옛 Streamlit MVP (wave-3, 2026-05-18 에 폐기됨). 회고용 잔존.
- **`frontend/`** — **현재 메인 UI**. Next.js 16 App Router + React 19 + Tailwind v4 + react-force-graph-3d + three.js. 페이지: `/` (graph) + `/ingest` + `/search` + `/cleanup` (D12) + `/settings`.

모든 모듈은 같은 venv + 같은 Postgres + 같은 Qdrant 공유. 단일 docker compose, 단일 배포 단위.

### 외부 프로젝트는 "벤치마킹 참조" 전용

`external/{openclaw,hermes-agent,hermes-webui}/` 는 gitignored clone. **셋 다 MIT 라이센스** 이므로 AGPL v3 와 호환 — 코드 vendor 가능하다 (단 LICENSE/copyright notice 보존 필수). 다만 실무 권장:

- **부분 코드 vendor** OK — license attribution 보존, 출처 주석 필수 (예: `# Adapted from hermes-agent/agent/skills.py (MIT) — Copyright (c) 2025 Nous Research`)
- **통째 fork** 비권장 — 의존성 무거움, 우리 구조와 안 맞음, 업스트림 추적 부담
- **일반적으로는 idea/UX 패턴 차용 후 자체 구현** — 가장 가볍고 유지보수 쉬움
- **vendor 한 코드는 LinkMind repo 안에 복사** — `external/` 는 gitignored 이고 언제든 삭제될 수 있으므로 source path 로 import 절대 X. 복사 후 LinkMind 코드 트리에서 자족적으로 동작해야.
- **라이센스 우회를 위해 함수명/변수명만 바꾸는 행위 금지** — 법적으로 derivative work 인정됨 (cosmetic 변형은 우회 불가) + MIT 는 attribution 만으로 완전 자유라 우회 자체가 불필요. 정직한 attribution 이 합법 + 안전 + 평판.

→ "from scratch 가 비효율적이면 적극 vendor (attribution 보존), 가벼우면 재작성" 의 사용자 판단.

흡수한/흡수할 패턴:
- **hermes-agent multi-channel gateway** → `ai_agents/` 의 ChannelAgent ABC + telegram/slack/whatsapp/discord 단계적 추가 (Phase 2.5 base, Phase 3+ 실제 채널)
- **hermes-agent `plugins/` 모양** → `backend/ingest/` 의 가벼운 정리 (auto dispatcher 명확화). ABC 강제까진 over-engineering.
- **hermes-agent 자가학습 (auto-skills)** → 자동 prompt/ingester 개선 (Phase 3+)
- **hermes-webui 세 패널 + SSE + vanilla JS** → `frontend/` (Phase 2.5)
- **openclaw onboard daemon** → 향후 systemd/launchd 등록 helper (Phase 3+)

### LLM provider 책임 분리

LLM provider 추상화는 backend 의 책임. `ai_agents/` 모듈은 LLMProvider 를 직접 호출하지 않는다 — 필요하면 backend HTTP `/ask` 통해서. 이유: agent 가 직접 LLM 부르면 model/prompt 버전 추적이 두 곳으로 갈라짐 (§2 Versioned analysis 원칙 위반).

## 4. 기술 스택 / 환경

- **OS**: Ubuntu, **GPU**: NVIDIA RTX 4090 (CUDA), **Docker**: nvidia-container-toolkit
- **Backend**: Python 3.11+ (검증: 3.13.12 + torch 2.6.0+cu124, NVIDIA driver 580.x), FastAPI, SQLAlchemy 2.0 async + asyncpg, pydantic-settings
- **DB**: PostgreSQL 16 (관계형 + raw 본문) + Qdrant 1.12 (벡터)
- **Embedding**: sentence-transformers (bge-m3) → Phase 2 에 TEI 로 전환
- **LLM**: vLLM (Gemma 4 26B-A4B-AWQ — 기본, 한국어 위키 합성) / OpenAI / Anthropic / Ollama (provider abstraction). 모델·구동 설정은 DB(app_settings) + Settings UI 관리, `scripts/vllm_restart.sh` 재구동
- **Frontend**: **Next.js 16 App Router + TypeScript + Tailwind 4 + react-force-graph-3d + three.js** (frontend/, 일원화. Streamlit 은 Phase 2.5 wave-3 에서 폐기 — Settings/Ingest/Search/Graph 모두 frontend 의 페이지로 마이그레이션됨). 옛 frontend/ 폴더는 회고용으로 남김 (step5 가 시작 안 함).
- **Object storage**: 로컬 FS → MinIO (Phase 2)
- **Python 환경**: **venv** (conda 아님). 이유: 시스템 의존성은 Docker 가 격리하고, Python 패키지는 전부 표준 pip — conda 의 강점이 안 살음. 학습(Phase 3 sVLL)용 conda env 는 그 시점에 별도 생성해 책임 분리.

설정은 **모두 `env/dev.env` 환경변수** 로 관리. 코드에 비밀값/하드코딩 절대 금지. `os.environ` 직접 접근 금지 — 항상 `backend.config.get_settings()` 를 통한다.

## 5. 코드 스타일

- **Python typing 필수**, `from __future__ import annotations`
- **async/await 우선** (블로킹 호출은 `asyncio.to_thread`)
- **Pydantic schema** 로 외부 인터페이스 정의 (`backend/schemas/`)
- **FastAPI router 구조** (`backend/api/<feature>.py`)
- **함수 단위 분리 + 서비스 단위 모듈화**, 지나친 OOP 지양
- **주석은 한국어 OK**, 충분히 작성 (특히 "왜 이렇게 했는지" 가 중요한 곳)
- 변수/함수 이름은 영어 + snake_case 유지

## 6. MVP 원칙: 과한 추상화 금지

- 디자인 패턴 / generic architecture / 미래 가정 기능 **금지**
- 동작하는 MVP > Clean Architecture
- 단, **재배포·서버 이전·온프레미스 설치·SaaS 화 가능 구조**는 처음부터 유지 (env, 볼륨, compose, healthcheck 분리)
- 새 기능 제안 시 "MVP 에 정말 필요한가?" 를 먼저 묻고, 아니면 Phase 2+ 로 미룬다

## 7. Git / Commit 규칙

- **모든 commit 메시지는 한국어로 작성**. 영문 conventional prefix(`feat:`/`fix:`/`chore:`) 사용 금지.
  - 예: `"초기 scaffold: ..."`, `"수정: ..."`, `"리팩토링: ..."`
  - 본문도 한국어. 코드 식별자, 명령어, 외부 시스템명(Postgres, Qdrant, OpenClaw) 은 원문 유지
- ⛔ **`Co-Authored-By: Claude ...` 트레일러 절대 금지** (2026-06-02 사용자 지시). harness
  기본값은 이 트레일러를 붙이지만, 이 저장소에선 **매 commit/push 전에 반드시 빼야** 한다.
  안 그러면 GitHub Contributors 에 `claude` 가 다시 잡힌다. (과거 히스토리는 이미 purge 완료.)
  → 커밋 메시지 작성 시 Co-Authored-By 줄을 아예 넣지 말 것.
- `git push --force`, `git reset --hard` 는 명시적 지시 없으면 금지
- `--no-verify` 등 hook 우회 금지
- `.env`, `volumes/`, `external/`, `archive/`, `__pycache__/` 는 절대 commit 금지 (.gitignore 로 처리됨)

## 8. 자주 쓰는 명령어

셋업 스크립트는 `stepN_setup_*` / `stepN_check_*` 쌍 패턴을 따른다. 각 step setup 직후 같은 번호의 check 로 sanity 확인:

```bash
# step1: Python 베이스 환경 (.venv + torch cu124 + requirements)
bash scripts/step1_install_base_env.sh
source .venv/bin/activate
bash scripts/step1_check_base_env.sh

# step2_1: 호스트에 Docker + NVIDIA Container Toolkit 설치 (sudo, 한 번만)
bash scripts/step2_1_install_docker.sh          # docker-ce + compose v2 + nvidia-container-toolkit
# 그룹 적용 위해 새 셸 또는 'newgrp docker'
bash scripts/step2_1_check_docker.sh            # docker / compose / nvidia runtime / hello-world

# step2_2: LinkMind 인프라 (Postgres + Qdrant + Ollama + OpenWebUI)
bash scripts/step2_2_setup_infra.sh             # docker compose up + healthy 대기 (step2_1_check 사전 호출)
bash scripts/step2_2_check_infra.sh             # 4개 서비스 연결성 검증
# Phase 2 (TEI + MinIO):  bash scripts/step2_2_setup_infra.sh --phase2

# step3: Ollama 모델 (env/dev.env 의 OLLAMA_MODEL pull)
bash scripts/step3_setup_ollama.sh            # qwen2.5:7b pull + 동작 검증
bash scripts/step3_check_ollama.sh            # API + 모델 존재 + generate dry run
bash scripts/ollama_pull.sh qwen2.5:14b       # 다른 모델 추가 받기 (서브 유틸)
bash scripts/ollama_chat.sh "안녕"             # 한 번의 채팅 테스트 (서브 유틸)

# step4: Qdrant 컬렉션 (bge-m3 1.4GB 첫 다운로드)
python -m backend.jobs.init_qdrant
bash scripts/step4_check_qdrant.sh            # 컬렉션 + vector dim 일치

# step5: backend (uvicorn :8000) + frontend (Next.js dev :3001) + telegram watcher
# 한 명령으로 셋 다 (권장) — invite check 자동 실행 후 watcher 시작
# 시작 시 vLLM 컨테이너(linkmind-vllm + linkmind-vllm-embed)가 내려가 있으면 자동 기동
# (이미 떠 있으면 no-op). --stop 은 vLLM 까지 정지해 GPU VRAM 을 완전히 해제 (Docling/학습 등
# 다른 GPU 작업 가능). vLLM 자동 기동만 끄려면 LINKMIND_SKIP_VLLM=1 (정지는 항상 수행).
bash scripts/step5_run_dev.sh             # --stop / --status / --foreground / --skip-check
# 또는 수동 (디버깅용 — 셸 3개 필요):
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
cd frontend && npm run dev             # :3001
python -m ai_agents.telegram_inbox_watcher --daemon

# 테스트 — 카테고리별 (자세히는 9번 Testing 정책)
bash scripts/tests/total/run_all_local.sh      # 5 카테고리 다 (로컬)
bash scripts/tests/total/run_ci_simulation.sh  # CI 가 도는 것만 (push 전 점검)
bash scripts/tests/ci/step1_cpu.sh             # 4s, default suite
bash scripts/tests/ci/step2_embedding.sh       # 15s, MiniLM CPU
bash scripts/tests/local/step5_gpu.sh          # 10s, CUDA 필요

# URL 하나 수동 수집
python -m backend.ingest.url https://arxiv.org/abs/2401.01234

# ai_agents — Telegram inbox watcher (현재). Phase 3+ 에 slack/whatsapp/discord 추가
# 2026-05-25 — batch 구조 제거, yaml 순서대로 한 채널씩 [resolve → backfill → 다음] 순차.
python -m ai_agents.telegram_inbox_watcher --daemon       # 백그라운드 listen + 자동 ingest
python -m ai_agents.telegram_inbox_watcher --backfill      # 옛 메시지 일괄 처리

# invite 검증 + yaml/cache 자동 정리 (2026-05-25). 사용자가 텔레그램 앱에서 채널을
# leave/추방됐을 때 죽은 invite 식별 + 즉시 yaml/channel_id_cache.json 정리 (백업 생성).
# step5_run_dev.sh 가 watcher 시작 직전 자동 실행 — `--skip-check` 로 끔.
python -m ai_agents.check_telegram_invites                # 기본: 자동 정리 + .bak.<ts> 백업
python -m ai_agents.check_telegram_invites --dry-run     # 검증만, 파일 안 건드림
bash scripts/step5_run_dev.sh --skip-check                # watcher 시작 시 check 건너뜀

# (옵션) 외부 client 사용 — openclaw 별도 띄워서 LinkMind API client 로 쓰고 싶을 때만
# 자세한 셋업은 docs/agent_architecture.md §1 참고. 기본 사용엔 불필요.
# bash scripts/install_openclaw.sh

# Slack 데이터 import — slackdump (비공개 채널/DM 포함). 자세히는 docs/slack_setup.md
slackdump workspace list
slackdump workspace new -token "$SLACK_USER_TOKEN" -cookie "$SLACK_D_COOKIE" hkkim
bash scripts/slack_export.sh                                       # workspace 전체 export (files=true), archive/slack_export/full_<ts>/ + latest symlink

# Slack ingest — slackdump export → LinkMind backfill (Phase C wave-2, 2026-05-19~23)
# (D13 이후) 임베딩은 vLLM-embed HTTP 공유라 ingest 가 uvicorn GPU 와 경합하지 않음 →
# 예전처럼 --stop 으로 backend 를 내릴 필요 없음. ⚠️ 오히려 --stop 은 이제 vLLM 까지 정지하므로
# ingest 에 필요한 임베딩 서비스(vllm-embed)가 죽는다 — ingest 전엔 --stop 쓰지 말 것.
bash scripts/slack_ingest_all.sh                                   # 전체 워크스페이스 backfill (tqdm 진행률 + archive 하위에 issues manifest 자동 저장)
bash scripts/slack_ingest_all.sh --channel 가-공부-cuda-programming  # 단일 채널만 (디버깅)
bash scripts/slack_ingest_all.sh --force                           # 동일 hash 도 summary/tags 재계산
python -m backend.ingest.slack archive/slack_export/latest \
    --workspace-url https://w1710672365-sjj477000.slack.com         # CLI 직접 (스크립트 우회 시)
bash scripts/step5_run_dev.sh                                      # ingest 완료 후 backend/frontend 재기동

# vLLM-embed (D13, 2026-05-23~) — bge-m3 별 컨테이너로 띄워 OOM 영원히 해결.
# uvicorn / watcher / CLI 가 임베딩 모델 직접 로드 안 함, HTTP 공유.
docker compose --env-file env/dev.env -f compose/docker-compose.dev.yml \
    --profile vllm up -d                                            # vllm-llm + vllm-embed 둘 다
docker logs -f linkmind-vllm-embed                                  # 첫 시작 시 bge-m3 1.4GB HF 다운로드 모니터링
curl -s http://localhost:8002/v1/embeddings -H 'Content-Type: application/json' \
    -d '{"model": "BAAI/bge-m3", "input": "안녕"}' | jq '.data[0].embedding | length'  # 1024
# env/dev.env 에서 EMBEDDING_BACKEND=local → vllm 으로 swap 후 backend/watcher 재기동

# D10 wiki — 옛 23k pages 일괄 wiki body 합성 (사용자 직접 실행)
bash scripts/run_wiki_backfill.sh                          # nohup background, concurrency=4 (~1일)
bash scripts/run_wiki_backfill.sh --tail                   # 진행률 실시간 (tqdm)
bash scripts/run_wiki_backfill.sh --status                 # 요약 (wiki_pages 통계 + log 5줄)
bash scripts/run_wiki_backfill.sh --stop                   # graceful (현재 page 끝나고 ~90s)
bash scripts/run_wiki_backfill.sh --stop-force             # 즉시 SIGKILL
bash scripts/run_wiki_backfill.sh --concurrency 8          # 더 빠르게 (VRAM 여유)
bash scripts/run_wiki_backfill.sh --concurrency 1          # sequential (옛 방식)
bash scripts/run_wiki_backfill.sh --slug 'arxiv__%'        # 특정 slug 패턴만
bash scripts/run_wiki_backfill.sh --limit 100              # 100 page 만
bash scripts/run_wiki_backfill.sh --dry-run                # 처리 대상 sample 10
# 한 줄로 재시작 (옛 거 자동 SIGKILL) — 같은 명령으로 다시 호출만
bash scripts/run_wiki_backfill.sh

# arxiv 메타 DB (arxiv_papers) — 키워드 검색을 로컬 FTS 로(arxiv API rate limit 0). /admin/arxiv 가 사용.
# (1) 초기 대량 적재 — Kaggle Cornell dataset(2.7M, COPY) → 1회. (2) 증분 — OAI-PMH.
python -m backend.jobs.arxiv_import_kaggle <path/arxiv-metadata.json>   # Kaggle JSONL 대량 적재(staging COPY)
python -m backend.jobs.arxiv_harvest_oai                                # 증분 — watermark(app_settings.arxiv_oai_last_from)부터 오늘까지 upsert
python -m backend.jobs.arxiv_harvest_oai --from 2026-01-01              # 특정 날짜부터 강제
python -m backend.jobs.arxiv_harvest_oai --no-watermark                 # watermark 갱신 안 함(테스트)
# 동작: OAI datestamp(마지막 수정일) 순회 → 신규 INSERT + 버전 개정분은 upsert(updated 더 새면만 갱신).
#   watermark 저장은 '오늘-2일'로 겹쳐(경계 누락 0, upsert 라 중복 무해). 2026-06-05 전체 3.0M 적재 완료.
# 매일 자동 증분(cron 예시) — 새벽 3시 watermark 부터 수확:
#   0 3 * * * cd /home/hyunkoo/DATA/hdd8TB2/Local_AI/LinkMind && .venv/bin/python -m backend.jobs.arxiv_harvest_oai >> /tmp/oai_incremental.log 2>&1

# D10.6 — wiki 중복 정리 + 키워드 정규화 + 사진 figure 연결 (2026-05-29, 사용자 직접)
# 모두 dry-run 먼저 → 숫자 확인 후 실제 실행. idempotent (재실행 안전).
python -m backend.jobs.cleanup_duplicate_wikis --dry-run    # T1 self→외부ID merge + T2 phantom 삭제 + T4 self→개념 merge 미리보기
python -m backend.jobs.cleanup_duplicate_wikis             # 실제 정리 (T1+T2+T4 전부)
python -m backend.jobs.cleanup_duplicate_wikis --t4-only    # T4 만 — 일반 URL 논문 self_wiki → 개념(kebab) wiki merge (부분집합일 때만, 2026-06-04)
python -m backend.jobs.cleanup_duplicate_wikis --rehome-orphans  # native wiki 없는 self_wiki 를 정체성 slug 로 재배치
python -m backend.jobs.normalize_keywords --dry-run        # 전체 wiki 키워드 정규화 미리보기 (CJK 삭제/camelCase/약어/별칭)
python -m backend.jobs.normalize_keywords                 # 실제 적용 (DB 의 약어·별칭 config 사용)
python -m backend.jobs.link_photo_captions --dry-run       # '사진+URL caption' → 본문 wiki figure 연결 미리보기
python -m backend.jobs.link_photo_captions                # 실제 연결 + standalone 사진 위키 삭제 (raw 이미지 보존)
python -m backend.jobs.link_photo_captions --delete-standalone  # URL 없는 사진 item + self_wiki 삭제 (raw 보존)

# vLLM 모델/설정 관리 (DB 일원화 2026-05-30) — Settings UI 에서 model/dtype/메모리 편집 후 재구동
bash scripts/vllm_restart.sh --dry-run     # DB(app_settings)의 vLLM 설정만 확인 (재구동 X)
bash scripts/vllm_restart.sh               # DB 값으로 vLLM 컨테이너 재구동 (force-recreate)

# Gemma 전환(2026-05-30) 후 중국어 정리 백필 — dry-run 먼저, idempotent
bash scripts/run_summary_backfill.sh --only-foreign   # 중국어 섞인 item.summary 만 Gemma 재생성
bash scripts/run_foreign_wiki_backfill.sh             # 중국어 섞인 wiki body 재생성
bash scripts/run_description_backfill.sh              # wiki title/description 을 body 의 #헤더·TL;DR 로 정제

# 신규 ingest 자동 wiki 흐름 (사용자 X — backend lifespan daemon 자동):
#   텔레그램 → /ingest/url → analysis_worker (summary) → classifier (wiki 매핑 + stale)
#   → wiki_writer_worker daemon (stale 만 자동 합성, ~15s/page)
# daemon 끄기: env LINKMIND_WIKI_WRITER_DAEMON=0 후 step5 재시작
```

## 9. Testing 정책 — 새 함수 추가 시 동반 작성 필수

**핵심 규칙**: 새 기능 / 함수를 추가하거나 기존 함수의 동작이 바뀌면 같은 PR/commit
안에서 단위 테스트도 함께 작성하거나 갱신한다. 회귀 방지 + CI 보장.

### 카테고리 5 종 (마커)

| 마커 | 위치 | 어디서 도는가 | 비고 |
|---|---|---|---|
| `cpu` (마커 없음) | `tests/*.py` | CI + 로컬 (가장 빠름, ≈4s) | pure unit + mock + fixture |
| `embedding` | `tests/embedding/` | CI + 로컬 | 가벼운 MiniLM-L6-v2 (~80MB), CPU 가능 |
| `integration` | `tests/integration/` | backend live (로컬), CI 에선 skip | FastAPI e2e — fixture 가 backend 미가동 시 pytest.skip |
| `llm` | `tests/llm/` | Ollama live (로컬), CI 에선 skip | 실 LLM 호출 sanity — fixture 가 미가동 시 skip |
| `gpu` | `tests/gpu/` | 로컬 (RTX 4090) 전용 | CUDA device 강제, CI 자동 deselect |

### 새 함수 추가 시 결정 흐름

1. **pure 함수 (DB/네트워크 없음)** → `tests/` 직접 추가, 마커 없음. 기존
   `test_external_ids.py` / `test_pdf_abstract.py` 같은 형태.
2. **외부 모듈 호출 (httpx, yt_dlp, GitHub API …)** → monkeypatch 로 가짜 응답
   주는 mock test. `tests/test_github_ingest_mocked.py` 패턴 참고.
3. **DB/Postgres 가 필요** → `tests/integration/` + `@pytest.mark.integration`.
   backend fixture 로 응답 contract 만 검증 (데이터 내용 X — DB 상태 의존성 피함).
4. **sentence-transformers / 임베딩** → `tests/embedding/` + `@pytest.mark.embedding`.
   가벼운 MiniLM 로. bge-m3 같은 무거운 production 모델 X.
5. **CUDA 강제** → `tests/gpu/` + `@pytest.mark.gpu`. torch.cuda.is_available()
   체크 fixture 로 GPU 없으면 skip.
6. **실 LLM 호출** → `tests/llm/` + `@pytest.mark.llm`. Ollama health 체크
   fixture + 짧은 호출 (max_tokens=10).

### 테스트 스크립트도 동기화

새 카테고리에 첫 테스트를 추가했으면 `scripts/tests/` 의 해당 스크립트가
자동으로 잡아준다 (marker 기반). 새로운 카테고리 자체를 만들면:
- `pytest.ini` 에 marker 등록
- `scripts/tests/_lib.sh` 에 `run_<category>()` 함수 추가
- `scripts/tests/ci/` 또는 `scripts/tests/local/` 에 stepN 스크립트
- `scripts/tests/total/run_all_local.sh` 및 `run_ci_simulation.sh` 에 호출 추가
- `scripts/tests/README.md` 표 갱신

### CI 와의 매핑

GitHub Actions (`.github/workflows/ci.yml`) 는 `pytest -m "not gpu"` — GPU 만
deselect. 나머지는 시도하되 환경 미충족 시 fixture skip. CI 가 cpu + embedding +
integration + llm 카테고리에 한해 회귀 잡음.

### 명령

```bash
bash scripts/tests/total/run_all_local.sh       # 5 카테고리 다 (로컬)
bash scripts/tests/total/run_ci_simulation.sh   # CI 가 도는 것만
.venv/bin/pytest -m '' tests/                   # marker 무시 전체
.venv/bin/pytest -m embedding tests/            # 단일 카테고리
```


## 10. 디렉토리 구조 요점

```
backend/
├─ api/         # FastAPI routers (health, ingest, search, ask, graph, settings, files)
├─ config.py    # pydantic-settings 환경설정 (모든 env 진입점)
├─ db/          # connection.py, repository.py, schema.sql
├─ embedding/   # base.py, local.py, vllm_embed.py (D13), factory.py, qdrant_store.py
├─ llm/         # base.py, factory.py, openai/claude/ollama provider
├─ ingest/      # source 별 (url/, slack/, telegram/, pdf/, github/, arxiv/, youtube/)
│              #   + auto dispatcher (host 기반 라우팅)
├─ schemas/     # Pydantic 요청·응답 모델
├─ storage/     # local.py (raw 파일 무손실 보존)
├─ utils/       # chunking, hashing, external_ids
├─ jobs/        # batch (init_db / init_qdrant / backfill_* / seed_* / generate_*) — `python -m backend.jobs.<name>`
└─ main.py      # FastAPI 진입점

ai_agents/                 # LinkMind 자체 multi-channel gateway (§3)
├─ base.py                 #   ChannelAgent ABC (setup / listen / on_message → ingest)
├─ telegram_inbox_watcher.py    # 현재 (ChannelAgent 상속)
├─ slack_inbox_watcher.py       # Phase 3+
├─ whatsapp_inbox_watcher.py    # Phase 3+
└─ discord_inbox_watcher.py     # Phase 3+

frontend/app.py            # 옛 Streamlit MVP — wave-3 (2026-05-18) 에서 폐기됨. 회고용 잔존.
frontend/               # Next.js 16 + React 19 + Tailwind v4 + react-force-graph-3d (현재 메인 UI)
├─ app/                    #   App Router pages (D11 후): /, /ingest, /ask, /wiki, /wiki/[slug], /settings
│                          #   (cleanup/search 폐기 — D11 에서 wiki 중심으로 통합)
├─ components/             #   GraphView, TopicsTree, ItemDetails, NodeDetails, KeywordsEditor, wiki/*
├─ lib/                    #   api.ts, i18n, colors
└─ types/                  #   TypeScript schemas (graph.ts 등)

compose/docker-compose.*   # docker compose 정의
env/                       # dev.env(gitignored) / dev.env.example
scripts/                   # 실행용 .sh 만 — stepN_*.sh / install_openclaw.sh / ollama_pull.sh / slack_export.sh
docs/                      # agent_architecture.md, training_data_design.md, features_backlog.md, slack_setup.md, telegram_setup.md
archive/                   # raw 자료 (gitignored)
volumes/                   # 컨테이너 영속 볼륨 (gitignored)
external/{openclaw,hermes-agent,hermes-webui}/  # gitignored 벤치마킹 참조 clone (코드 import 금지)
```

## 11. NEVER 목록

- ❌ `.env` 파일 commit
- ❌ commit 메시지 영어 / conventional prefix
- ❌ license 호환 안 되는 외부 코드를 LinkMind 에 vendor (GPL → AGPL 호환, MIT/Apache → AGPL 호환, AGPL→AGPL 호환, BSL/proprietary → 금지).
- ⚠️ MIT/Apache 코드 vendor 시 LICENSE 파일 + copyright notice + 출처 주석 (`# Adapted from <repo>/<file> (MIT) — Copyright (c) ...`) 필수. `external/` 의 clone 자체는 gitignored 유지.
- ❌ `raw_content` 를 NULL 로 두거나 변형해서 저장
- ❌ AI 분석 결과를 model/prompt 버전 없이 저장
- ❌ `os.environ.get(...)` 코드에서 직접 사용 (`backend.config` 경유)
- ❌ 이미지/PDF resize·compress (학습 데이터 손실)
- ❌ `--force` / `--no-verify` / `reset --hard` 같은 destructive 명령 자의로
- ❌ **사용자 데이터로 운영자의 공통 모델 학습** (privacy 침해 + GDPR/PIPA 위반 + training data extraction attack). personal LoRA 는 사용자 본인 데이터로 본인 모델만 (§14 Privacy 5원칙).
- ❌ `ai_agents/` 모듈이 backend LLMProvider 를 직접 호출 — HTTP `/ask` 경유 (§3 책임 분리 + §2 Versioned analysis).
- ❌ hosted SaaS (Phase 7+): cross-tenant 데이터 노출 (RLS / tenant_id 필터 누락)
- ❌ hosted SaaS: 사용자 데이터를 동의 없이 외부 LLM API (OpenAI/Anthropic) 에 전송 — BYOK 또는 명시적 약관 동의한 경우만.

## 12. Phase 별 로드맵

> ★ **이 표가 Phase 번호의 single source of truth** (README 와 동일). 2026-06-02 재번호:
> 옛 `2`+`2.5` 병합 → `2`, 완료된 wiki → `3`, 이하 한 칸씩 밀림. 이 파일 다른 절·`docs/`·
> 메모리의 옛 번호(예: "Phase 4 = LoRA", "Phase 2.5", "Phase 3 sVLL/OCR")는 **레거시이니
> 무시하고 이 표를 따른다.**

| Phase | 상태 | 핵심 |
|---|---|---|
| 1 | ✅ 완료 | Postgres + Qdrant + URL ingest + Embedding + Semantic Search + RAG |
| 2 | ✅ 완료 | AI 요약/태깅, Slack export 파서, 임베딩 인프라(vLLM-embed), 카테고리 강화, Topic 그래프, ChannelAgent ABC, Next.js 16 UI, modality-aware viewer, 3-tier categories, Telegram multi-channel |
| 3 | ✅ 완료 | **llm_wiki 시스템** — classifier/retriever/writer agent, wiki API + Qdrant body search, wiki 리스트/상세 UI + KeywordsEditor, writer daemon + backfill, "1 링크 = 1 위키", 키워드 정규화/클라우드, 사진 figure 연결, 대화형 /ask(Step 1) |
| 4 | 🚧 진행 | **멀티테넌트(조직 space/멤버발급/force-change/권한/대화프라이버시) ✅**, 대화형 /ask 멀티턴 ✅ + **하이브리드 RAG(위키 본문 통합) ✅**, **논문 writer 재설계 ✅**(Docling raw→논문구조+inline 그림·표+한글캡션) + **중복 위키 정리(T4)·예방·de-clone ✅** + 위키 렌더(GFM 표/LaTeX), **키워드 기반 arxiv 수집→위키(admin, MVP) ✅**(로컬 arxiv 메타 DB FTS + 3패널 UI + 위키 유무 필터), **writer 큰논문 토큰 안정화 ✅**(map 압축+output 클램프), **classifier 연관위키 재합성 방지 ✅**, **PDF surrogate ingest fix ✅**(arxiv 2605.29583); **(다음) arxiv URL/첨부 중복 방지 → writer 합성 검증 → Docling VRAM 경합 fix**, 실제 채널 확장(slack/whatsapp/discord), 자가학습(feedback table), critic agent |
| 5 | ⬜ 미시작 | **sVLL LoRA 파인튜닝** (Gemma 4 26B-A4B QLoRA 또는 12B dense), dataset exporter, vLLM/Ollama 서빙 |
| 6 | ⬜ 미시작 | Continuous training loop, 온프레미스 AI 엔진 완성 |
| 7 | ⬜ 미시작 | OSS(AGPL v3) 공개 → hosted SaaS (Auth.js + Stripe, multi-tenant, BYOK). §14 참조 |
| 8 | **T.B.D** | **멀티모달 VLM** — figure 이미지 자체를 모델이 **보고** 이해·설명 (caption 없는 그림 / 그림 Q&A / OCR). Gemma 4 12B 네이티브 멀티모달 또는 SmolVLM2. 원본 이미지는 이미 무손실 보존(§2)이라 언제든 착수 가능 → 필요성 확인 후 결정. |


## 13. 기능 현황 — 개발한 / 개발할(순서) / 보류(이유)

> **날짜별 히스토리 금지** (사용자 명시). 이력은 **git log** + **docs/features_backlog.md** 가
> source of truth. 여기는 세 리스트만 — ① 구현된 기능 ② 개발할 기능(순서) ③ 보류된 기능(이유).
> 재개 운영 정보는 memory `project_next_session_entrypoint`.

### 구현된 기능 (현재 main)

**수집 (ingest)**
- URL / PDF / DOCX / PPTX / TXT / MD / GitHub / arxiv / YouTube(영상·playlist·channel) / 이미지 — host 자동 라우팅 dispatcher
- raw-first 무손실 저장 + provenance + idempotent (UNIQUE hash) + 첨부 SHA-256 dedup 영구 보존
- 본문 추출 실패해도 raw + URL 보존 (OG meta fallback / YouTube oEmbed fallback / fetch_error 마킹)
- PDF 추출물 surrogate-safe: `_sanitize_text` = NUL 제거 + split/lone surrogate 복원(`utils/text.repair_surrogates`). Docling/pypdf 가 수학 볼드(astral) 문자를 surrogate 로 흘려보내도 utf-8/Postgres 인코딩에서 ingest 가 안 죽음 (arxiv 2605.29583 케이스)
- 텔레그램 multi-channel inbox watcher (yaml 단일 진실, FloodWait/cache, 처리 성공 시 메시지 삭제)
- Slack export 일회성 backfill (thread / mrkdwn / 첨부 파서)
- AI 요약 (한국어 bullet, Gemma 4) + 임베딩 (bge-m3, vLLM-embed HTTP 공유). tags 폐기 (2026-05-30) — #tag 검색(/search) 폐기 + 위키 키워드로 대체, items.tags 비움

**wiki (LinkMind 정체성 — D10/D11/D10.6)**
- 4 agent: classifier(자료→wiki 매핑) / retriever / writer(문서타입별 markdown 합성) / critic(stub)
- 자동 흐름: 신규 ingest → analysis_worker(summary) → classifier(매핑+pending) → writer daemon(자동 합성)
- **문서타입별 writer**: 논문(arxiv/pdf)은 **논문 구조**(개요/핵심 기여/방법/실험·결과/결론)를 **Docling raw markdown 발췌 기반**으로 합성(summary 아님 — 원문 없이 이해되게 충실하게). 그림은 **본문 맥락 속 inline 배치**(전체개요그림→개요, 아키텍처→방법, 결과→실험·결과: LLM 이 `[FIGN]` placeholder 두면 코드가 실제 이미지로 치환, file_hash 로 URL 결정론적), 표는 inline(셀 영어 보존+캡션 한글), figure 캡션은 한글 1문장 요약(번호 유지). 일반 자료는 개념형 구조.
- "1 링크 = 1 위키": confidence≥0.9 자기 정체성 topic 만 primary, native_identity_external_id 보장. classifier 가 item 이 개념/외부ID 위키에 이미 연결되면 **중복 self_wiki 생성 스킵**(예방). self-wiki 는 **정체성 item 중심**(제목/본문/그림) — cross-link 논문에 납치 안 됨(de-clone).
- wiki UI: list (status tab + 정렬 + 페이지당 라디오 + pagination + ETA + 7필드 검색) + detail (본문 / Sources / Keywords). **본문 렌더는 react-markdown** (GFM 표 + LaTeX KaTeX + [[slug]] wikilink + [N] citation + figure 이미지)
- 키워드: 정규화 (영문만 + camelCase + 약어/별칭, Settings+DB 편집) + 클라우드 사이드바 (빈도순 + ⭐ + 다중 AND filter)
- 사진: '사진+URL' = 본문 figure link, standalone 사진 위키 정리 (raw 보존)
- 도구: backfill (concurrency 4) + 정리 job (cleanup_duplicate_wikis — **T1 self→외부ID / T2 phantom / T4 self→개념(부분집합) merge** / normalize_keywords / link_photo_captions / cleanup_overlinks)

**arxiv 수집 (admin 전용 — `/admin/arxiv`)**
- **로컬 arxiv 메타 DB** (`arxiv_papers`, Postgres FTS): Kaggle bulk(2.7M) + OAI-PMH 증분 적재 → 키워드 검색이 **로컬**이라 arxiv API rate limit(429) 0. 독립 패키지 `arxiv_harvester`(MIT, OSS 추출용) in-process import (search/build_query/oai/filters).
- **관심/구독 키워드** (`collection_keywords`: user_id·space·keyword·group_label·enabled) — per-user 등록 + **대표 키워드(그룹) hierarchy**, 위키 키워드와 별개.
- **3패널 UI**(좌: 분야 필터 + 검색상태 + 대표키워드 그룹 + 결과 내 검색(refine) + 위키 유/무/전체 라디오 / 중: 논문 리스트(서버 페이지네이션 10/p) / 우: 위키 inline 상세 or arXiv PDF iframe). 패널 폭 **유저별 DB 저장**(`user_ui_prefs`).
- 논문 카드: **🔭 arXiv**(PDF 우측 패널) · **📖 위키**(완료 시) · **📥 수집**(미수집) 3-state 버튼 + 일괄 수집(동시 수집). 수집 = arxiv_id→`arxiv.org/pdf` ingest→classifier→writer. 분야(archive) 다중선택+한글 라벨, 위키 유/무 필터(서버), 키워드 union 검색(상한 없음).

**UI (Next.js 16 + React 19)**
- 페이지: `/`→`/ask` redirect · `/ask` (대화형 RAG Step 1, 메인·홈) · `/wiki` (리스트 + 우측 inline 상세) · `/admin/arxiv` 🔭 (arxiv 수집, admin 전용) · `/ingest` · `/settings`. nav 순서: Ask·위키·arXiv·수집·설정.
- `/ask` 3-패널(사이드바[프로젝트+최근, localStorage 영속]/채팅/위키, 경계 드래그 리사이즈) + **URL-paste-ingest**: URL 붙이면 자동 ingest → 그 자료 native 위키 우측 표시(없으면 "생성 중"+pending 탭) + 관련위키 배지 live 갱신. (`GET /wiki/by-item` native 우선 · `POST /wiki/statuses`)
- `/ask` **멀티턴 대화**: history 를 요청에 실어 맥락 유지 + 후속질문 쿼리 재작성(condense) + **SSE streaming**(`POST /ask/stream`, meta→token→done, live 렌더 + 완료 시 commit). 세션 전환/언마운트 시 AbortController 로 stream 취소. vLLM 만 진짜 token streaming.
- `/ask` **하이브리드 RAG (2026-06-03)** ★: 답변에 위키 본문(`linkmind_wiki_pages`) + item chunk + pinned 를 함께 활용. `_retrieve_wikis`(completed 위키 top3 의미검색→DB 본문) → context 상단(정리된 지식 우선) + `_merge_searched_wikis`(related_wikis 병합). 정리해둔 위키를 실제 답변에 써먹음. 테스트 `tests/test_ask_hybrid_wiki.py`.
- `/ask` **대화 프라이버시 + 서버 동기화 (2026-06-03)**: 세션/메시지는 **소유자 본인만**(admin 도 X), 프로젝트는 조직 공유. **계정별 localStorage**(`:userId`) + 서버 동기화 — `PUT /sessions/sync`(write-through 미러) · 로그인 시 `GET /sessions/export`(복원) · 로그아웃 시 캐시 정리. 프로젝트별 "새 대화" 버튼.
- `/wiki`: 키워드 cloud + status/정렬/검색 필터 + 카드 클릭 → **우측 패널에 상세 inline** (`WikiDetailView` — 편집/재합성/Sources/Relationship/Keywords/메타/삭제 전부. `/wiki/[slug]` 단독 페이지와 공용)
- `/graph` (keyword▸wiki▸item + 실시간 co-occurrence) 는 **보류** — 메인 nav 제거 (키워드 49,919 규모에 force-graph 효용 낮음). 코드·endpoint 남김, URL 직접 접근만.
- i18n (한/EN) + ThemeToggle (☀️/🌙/🖥) + 자료/위키 삭제 (2단계 confirm, Qdrant + Postgres CASCADE, raw 보존)

**멀티테넌트 / 인증 (2026-06-03)** ★
- **1조직 = 1space 데이터공유**, self-signup 없음 (루트 발급/bootstrap). space = 격리·학습·책임 단위.
- 인증: id/pw 로그인 + **JWT httpOnly 쿠키** + 전 데이터 API 보호(`Depends(get_current_user)`, health/auth/files 제외). `backend/auth/security.py`(bcrypt+JWT), `backend/api/{deps,middleware,auth}.py`.
- **bootstrap**: user 0명이면 브라우저 `/login` '조직 만들기'로 첫 관리자 + 조직(space) 생성. 그 후 비활성.
- **멤버 발급**: 루트(owner/admin)가 Settings(루트전용 `require_space_admin`)에서 이메일+초기비번 발급 → 멤버 첫 로그인 시 **force-change**(비번/이메일 강제 변경, `must_change_password`).
- **권한 게이팅**: Settings 전체 루트전용 (LLM/프롬프트/키워드/멤버 = 조직 전역 영향). member 는 nav 에서 설정 숨김.
- **대화 프라이버시**: `ask_sessions.user_id` 소유자만 조회(admin 도 X), 프로젝트=조직 공유, 학습=space 전체 (보기 권한 ≠ 학습 사용). `backend/api/sessions.py`.
- **배포모델**: 조직서버 1대(backend+GPU+DB) + 웹/데스크탑(Tauri) thin client (유저 PC 는 GPU 불필요). **단계 C(데이터 space_id+RLS) 영구 폐기** — 멀티조직 금지(보안), 1조직=1인스턴스 = 인스턴스 자체가 격리경계.
- 약관 초안 `docs/data_responsibility.md` (조직 데이터 책임 + 운영자 면책). 계정: owner `hyunkookim.me@gmail.com` / member `studian@gmail.com`, 조직 `real2real`.

**인프라**
- Postgres 16 + Qdrant + vLLM (Gemma 4 26B-A4B MoE-AWQ, KV cache fp8 + 16384 context, 멀티모달 끄기) + vLLM-embed (bge-m3) — GPU HTTP 공유 (RTX 4090 단일). 2026-05-30 Qwen 에서 교체: 중국어 native bias 근본 제거.
- **vLLM 모델/구동 파라미터는 DB(app_settings)에서 관리** — Settings UI 편집 + `scripts/vllm_restart.sh` 로 DB 값 읽어 컨테이너 재구동. env 의 VLLM_* 는 fallback 만 (제거됨).
- docker compose 단일 배포 + VOLUMES_ROOT env + step1~5 setup/check 스크립트
- 테스트 5 카테고리 (cpu/embedding/integration/llm/gpu). **CI 는 cpu-only**(gpu/embedding/integration/llm 디렉토리 collect 제외 — 무거운 ML/인프라 회피, 회귀는 로컬 전체 테스트가 잡음). 627 cpu PASS

### 현재 동작 핵심 개념 (인지 필수)

| 개념 | 내용 |
|---|---|
| status 3종 | `issues` (잔여) / `pending` (처리 큐) / `completed` (클릭 가능) |
| sub-state | `body_processing_started_at` — pending + 5분 안 = generating, 그 외 = queuing |
| daemon = batch | concurrency env `LINKMIND_WIKI_WRITER_CONCURRENCY`(기본 2, 큰 논문 KV cache 압박 완화). on/off env `LINKMIND_WIKI_WRITER_DAEMON`(**현재 OFF** — 내일 writer 검증 후 재개). 둘 다 `issues + pending` 처리. |
| classifier | self_wiki(`url__item__`)는 fallback 만 — item 이 개념/외부ID 위키에 연결되면 중복 self_wiki **스킵**(예방). 신규 item 이 매칭된 위키 중 **자기 self/identity/신규/figure 만** stale(재합성), matched(기존 cross-link)는 link 만 — 1건 ingest 가 연관 기존 위키를 무더기 재합성하던 것 방지(2026-06-05). |
| writer 문서타입 | 논문(arxiv/pdf, raw≥400자)=논문구조+raw발췌+inline그림([FIGN])+표. 일반=개념구조. self-wiki 는 정체성 item 중심 — cross-link 논문에 납치 X(de-clone). 큰 논문은 raw 를 섹션별 **map 압축**(depth 5)으로 context(16384) 안에 들이고 **output 동적 클램프**(`_clamp_output_tokens`)로 토큰 초과(영구 합성 실패) 방지 — 짤림 없이 충실. |
| arxiv 수집 | `/admin/arxiv`(admin) — 로컬 `arxiv_papers` FTS(rate limit 0) + 그룹 키워드 union 검색(상한 없음) + 위키 유/무 필터 + 3-state 버튼(arXiv PDF/위키/수집). |
| lazy 합성 제거 | GET /wiki/{slug} 는 합성 X. `?regenerate=true` 만 LLM. completed 만 클릭. |
| 1 링크 = 1 위키 | confidence≥0.9 자기 정체성 topic 만 primary wiki. cross-modal 단서(0.7)는 link 만. native_identity_external_id 가 상류 보장. |
| 키워드 정규화 | 영문만 (CJK/한글 삭제) + camelCase + 약어/별칭 (`backend/utils/keywords.py`). 목록은 Settings + DB (`/settings/keywords`). |
| 사진 처리 | 사진+URL = figure link. 사진만 메시지 = ingest 안 함 (텔레그램에 남김). raw 이미지 항상 보존. |

### 개발할 기능 (순서 — 다음 세션부터)

> 전략: (완료) 멀티턴 ask → 멀티테넌트 → 하이브리드 RAG → 논문 writer 재설계·중복 위키 정리 →
> arxiv 수집 admin 페이지(MVP) → **(다음) writer 합성 안정화 검증 + Docling VRAM 경합 fix → 자가학습 → 학습(Phase 5)**.
> 사용자 페이스 공격적, 빠르게 이어 추진. (기간 명시 금지)
>
> **현재 위치 (2026-06-15 갱신)**: ① 멀티턴 ✅ → ② 멀티테넌트 ✅ → 논문 writer 재설계·중복 위키 정리 ✅ →
> ③ 키워드 기반 arxiv 수집 admin 페이지 (MVP) ✅ → ④ **PDF surrogate ingest 영구 실패 fix ✅**(arxiv 2605.29583 — Docling/pypdf
> 가 수학 볼드 astral 문자를 surrogate 로 흘려 utf-8 인코딩에서 ingest 통째로 죽던 것; `utils/text.repair_surrogates` +
> `_sanitize_text` 통합. 텔레그램 msg 재처리→ok=True→자동 삭제 검증. commit `4e45321`) →
> **다음(내일, 순서 확정) = ① arxiv URL/첨부 중복 방지 → ② writer 합성 검증 → ③ Docling VRAM 경합 fix**. 상세는 메모리 [[project_next_session_entrypoint]].
> 배포모델: 조직서버 1대 + 웹/데스크탑(Tauri) thin client. **단계 C(데이터 space_id+RLS) 영구 폐기** — 1조직=1인스턴스라 인스턴스 자체가 격리경계.
> (① 멀티턴 · ② 멀티테넌트 · 논문 writer 재설계 · 중복 위키 정리 · arxiv 수집 MVP · surrogate fix 모두 완료 — §13 구현된 기능 참조.)

1. **arxiv URL/첨부 중복 방지** ★ 내일 1순위 — 규모 **작음**, 리스크 낮음
   - 증상: 한 텔레그램 메시지에 arxiv URL + 같은 논문 PDF 첨부가 같이 오면 item 2개·wiki 2개로 쪼개짐
     (URL→`ingest_pdf` Docling=`arxiv:<id>` / 첨부→`ingest_document` pypdf=다른 hash=`url:item:<uuid>` self-wiki).
     `UNIQUE(source_type, raw_content_hash)` 는 raw 가 달라 못 막음.
   - **현실 케이스 단순화**: URL 없는 맨첨부는 ingest 안 됨([telegram:255]) → 첨부는 **항상 같은 메시지 URL 동반** →
     텔레그램 메시지 단위 dedup 으로 완전 커버. (전역 external_id dedup = 스키마 변경 = over-engineering, 보류.)
   - **설계(MVP, 후보 1)**: `ingest_telegram_message` 에서 URL 정체성(`extract_external_ids(url=...)` arxiv/doi) 수집 →
     각 첨부 파일명 정체성(`extract_external_ids(text=att.file_name)`)이 URL 정체성과 겹치면 **새 item 생성 스킵 + 그 PDF 는 URL item 에
     attachment 로 붙여 raw 무손실(§2)**. 정체성 없는 일반 첨부는 기존대로 ingest(보수적). 동반 mock 단위 테스트(§9).
2. **writer 합성 검증** ★ 내일 2순위 — 규모 중간(대부분 *검증*), 리스크 중
   - 큰 논문 16384 토큰 초과로 위키 합성이 *영구 실패*하던 버그 수정함(추정 보수화 `_CHARS_PER_TOKEN` 1.6 +
     overhead 예약 + output 동적 클램프 `_clamp_output_tokens` + output 6144 + map 압축 depth 5). 코드 수정만 됐고 **실제 합성으로 짤림
     없이 고품질 나오는지 미검증** — daemon 은 현재 OFF(`LINKMIND_WIKI_WRITER_DAEMON=0`). 내일 backfill 로 검증 후 daemon 재개.
   - 참고: writer 동시성 env `LINKMIND_WIKI_WRITER_CONCURRENCY`(현재 2). **위키를 내용 풍부하게(고품질) 만들면 합성이 무거워지니
     `1`(순차)로 낮춰 안정성 우선 고려** (사용자 2026-06-05 — 코드 변경 없이 env 값만). daemon/backfill 공통.
3. **Docling VRAM 경합 fix** ★ 내일 3순위 — 규모 **큼**, 리스크 높음
   - collect 한 PDF 가 item 으로 안 생기는 케이스 있음(예: `2501.11102` — PDF 다운로드 OK 인데 item 없음).
     vLLM 이 VRAM 97% 점유라 ingest 단계 Docling 이 GPU 못 잡아 실패 의심. **반드시 GPU 로 정상 동작하게** 해결 — Docling 배치 swap
     (vLLM 잠깐 내림→Docling GPU→vLLM 복귀, [[project_docling_vram_batch_swap]])이 방향. **CPU 강제는 안 함**(너무 느림 — 사용자 명시).
   - **(c) arxiv 메타 DB·데몬 분리** (사용자 2026-06-05 확정, 이 묶음 후 별건): `arxiv_harvester` 는 독립 패키지니 자기 DB·데몬을 가져야.
     현재 미분리(`arxiv_papers` LinkMind schema.sql, 적재/검색 backend). → **`arxiv_papers`(공개 3M편)만** 별도 DB `arxiv_meta` + 패키지가
     DDL/적재/검색/harvest 데몬(GPU 무관 CPU → cron) 소유. **collection_keywords·user_ui_prefs 는 LinkMind 유지**(user/space 의존).
     admin_arxiv 는 검색 클라이언트, collected 매칭은 items 앱 병합(SQL 조인 없어 분리 쉬움). Cornell 파서도 패키지로.
2. **자가학습 (auto-skills)** — *명령 없이* 자동 개선.
   - 암묵 feedback(다음 턴 뉘앙스/행동 → soft label) + 명시 교정(gold) → prompt/ingester 개선.
     raw 답변 hard 학습 금지. 상세 `docs/` + 메모리 ask_self_learning_design.
3. **학습 파이프라인 (Phase 5)** — *서빙(vLLM/Ollama)은 inference 만; 학습은 별도 Unsloth/QLoRA — 본인 데이터로 본인 LoRA (§11)*
   - feedback 인프라 + dataset exporter (raw + summary + user_notes + feedback → JSONL)
   - sVLL LoRA — **Gemma 4 26B-A4B QLoRA (RTX 4090) 또는 Gemma 4 12B(dense, QLoRA 쉬움)**. 운영=AWQ(vLLM)/GGUF(Ollama)
     inference, 학습=원본+QLoRA 분리 → merge → 재양자화. 상세 `docs/features_backlog.md` Phase 5. + continuous loop (Phase 6)
4. **(후보) figure 설명(VLM)** — figure 이미지 → 설명(SmolVLM2 등, 배치 GPU). + pypdf 추출 옛 논문 Docling 재처리(진짜 figure caption).

### 보류된 기능 (이유)

- **/graph 키워드 관계 그래프** — 키워드 49,919개 규모에 force-graph 효용 낮음 (점구름·클릭지옥).
  메인 nav 제거 + 코드·endpoint 보류 (`app/graph`, `backend/api/graph.py`). 본격화 vs 완전 삭제 추후 결정.
- **D10 wave-3 critic agent** — wiki 품질(citation/모순) 검증. ask 안정화 후.
- **D8 cross-modality matching** — wiki body 안에서 자연 처리. 우선순위 낮음.
- **D10 lint job** — 모순/stale/orphan 정기 점검. 데이터 안정 후.
- **link_photo_captions 자동화** — 현재 수동(idempotent). 필요 시 daemon.

**제어 명령**

```bash
bash scripts/step5_run_dev.sh                       # 전체 가동 (backend + frontend + watcher + vLLM 자동 기동)
bash scripts/step5_run_dev.sh --stop                # 정지 (vLLM 포함 — GPU VRAM 완전 해제)
curl -s http://localhost:8000/wiki/_meta/stats | jq # wiki 상태 (completed/pending/issues)
```

---

## 14. License & Privacy & SaaS path

### License — AGPL v3 (적용됨)

- **AGPL v3** — self-host 자유 (개인/회사 무제한), 단 변형해서 SaaS 로 재판매 시 변경 사항 공개 의무. Plausible/Cal.com/n8n 채택 모델.
- `LICENSE` 파일 = AGPL v3 공식 원문 (gnu.org 정본, verbatim). Copyright (C) 2026 Hyunkoo Kim (hyunkoome). repo 가 private 이어도 라이센스는 명시 — 공개 전환 시 그대로 유효.
- AWS / Notion 등이 LinkMindCloud 만들어 재판매하는 시나리오를 차단 — Elasticsearch 가 MIT 에서 SSPL 로 옮긴 이유.
- vendor 한 외부 MIT 코드(openclaw/hermes-*)는 해당 파일에 MIT attribution 주석 + 출처 보존 (§3, §11). MIT/Apache → AGPL 호환.

### Privacy 5원칙 (hosted SaaS 진입 시 적용)

1. **사용자 데이터로 공통 모델 학습 절대 금지** — §1, §11. personal LoRA 는 사용자 본인 데이터로 본인 모델만. 이게 LinkMind 의 핵심 차별점.
2. **Tenant isolation** — 모든 DB query 에 `tenant_id` 필터 + Postgres RLS (Row Level Security). cross-tenant search/ask 절대 X.
3. **LLM 호출 동의** — 사용자가 BYOK (Bring Your Own Key) 했거나 명시적 약관 동의한 경우만 외부 API (OpenAI/Anthropic) 에 사용자 데이터 전송.
4. **삭제 권리 (GDPR / 한국 PIPA)** — 계정 삭제 요청 시 30일 내 Postgres + Qdrant + R2 모든 데이터 영구 삭제. 모델 학습 결과 (만약 있다면) 도 함께 제거.
5. **사용자 ingest 책임** — 사용자가 합법적으로 접근 권한 있는 URL 만. hosted 에선 PDF 직접 업로드 비활성 (저작권 위험).

### SaaS path 단계 (순서만 — 기간/timeline 명시 금지)

> 기간 추정(개월/년 등) 적지 않는다 (2026-06-02 사용자 지시). 아래는 진행 *순서* 일 뿐.

- **Phase 7-A**: self-host 완성도. 본인 사용 + 데이터 누적.
- **Phase 7-B**: OSS 공개 (AGPL v3), ProductHunt / HackerNews / LinkedIn 런칭.
- **Phase 7-C**: closed beta hosted — Fly.io / Vercel / Cloudflare R2, Google OAuth, **BYOK 강제** (운영자 LLM 비용 0), waitlist.
- **Phase 7-D**: freemium 출시 — Free (월 한도 + BYOK) / Pro $9/월 / Team $19/사용자/월. Founding member 영구 50% 할인.
- **Phase 7-E**: Enterprise tier — 격리 GPU + personal LoRA + SSO + audit log.

### hosted vs self-host 기능 분리

- **hosted only**: web UI, multi-tenant auth, Stripe 결제, 운영자가 제공하는 Telegram bot
- **self-host only**: PDF 직접 업로드, personal sVLL LoRA fine-tuning, 자유로운 client 선택 (openclaw/hermes-agent/자체 봇)
- **공통**: URL/YouTube/GitHub ingest, search, ask, graph UI, topic 그래프

