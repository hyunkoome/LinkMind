# LinkMind

> **개인 데이터로 sVLL(small VLM/LLM) 을 학습시켜 온프레미스 AI 엔진을 만드는 것이 최종 목표**.
> LinkMind 는 그 학습 데이터를 raw-first 원칙으로 수집·구조화하는 **self-contained personal AI engine**.
> (이전 "LinkMind + OpenClaw 두 시스템 분리" 모델 → Phase 2.5 (2026-05-18) 부터 단일 self-contained 시스템으로 통합. external/ 는 벤치마킹 참조용.)

```
[Sources]                                  [ai_agents/ (LinkMind 내부)]
URL · PDF · DOCX · PPTX · TXT · MD ·       Telegram inbox watcher
GitHub · Arxiv · YouTube · Image           (Phase 3+: Slack/WhatsApp/Discord)
              │                                    │
              └────────────┬───────────────────────┘
                           ▼
                  [LinkMind backend]
                           │
              ┌────────────┼──────────────┐
              ▼            ▼              ▼
         Postgres       Qdrant        Storage
       (raw + 메모 +   (vectors)    (loss-less
        is_read +                    원본 파일)
        attachments)
                           │
                           ▼
                  FastAPI + Next.js 16 (frontend/)
                  + 3D Graph (react-force-graph-3d)
                  + /wiki UI (D10 wave-1+2 + D11 통합, 2026-05-27)
                           │
            ┌──────────────┼────────────────┐
            ▼              ▼                ▼
       /ingest    /ask (대화형 RAG)   /graph (3D force)
                  /wiki (검색·list·detail·편집·삭제)
                           │
                           ▼
        [D10 llm_wiki — 2026-05-26 wave-1+2 완료]
        - 4 agent (classifier + retriever + writer + critic stub)
        - wiki body 자동 합성 (vLLM Qwen2.5-7B, 5 섹션 markdown)
        - daemon = batch (concurrency 4, D11 통일)

        [D11 wiki 통합 — 2026-05-27 완료, 26 commits]
        - cleanup/search 페이지 폐기 → wiki 중심 통합
        - status 3종 (issues/pending/completed + sub-state)
        - classifier 1:1 fallback wiki (사용자 자료 1순위)
        - /ask 대화형 RAG (Step 1)
        - wiki list UX (4 tab + pagination + ETA + 7 필드 검색)
                           │
                           ▼
           (Phase 3 후반) dataset exporter
        — agent_runs + wiki_page_versions diff
                           │
                           ▼
        sVLL LoRA 파인튜닝 — 사용자 본인 데이터로 본인 모델만
                           │
                           ▼
         vLLM 으로 온프레미스 서빙 + LoRA hot-swap
                           │
                           ▼
            LinkMind LLMProvider 로 dogfooding
```

---

## 빠른 시작

### 0. 사전 요구사항

- Ubuntu (또는 WSL2), Docker 24+, NVIDIA Container Toolkit (RTX 4090 권장)
- Python 3.11+ (검증 환경: 3.13.12 + torch 2.6.0+cu124 / NVIDIA driver 580.x)
- `git`, `make` (optional)

### 1. 저장소 clone & 환경 파일

```bash
git clone git@github.com:Real2Real-AI/LinkMind.git
cd LinkMind

cp env/dev.env.example env/dev.env
$EDITOR env/dev.env   # POSTGRES_PASSWORD, OPENAI_API_KEY 등 채우기
```

셋업은 단계별 `stepN_setup_*` / `stepN_check_*` 쌍으로 구성된다. 각 step 의 setup 직후 같은 번호의 check 를 돌려 sanity 확인 후 다음 step 으로 넘어가는 흐름.

### 2. Python 베이스 환경 (step1)

`scripts/step1_install_base_env.sh` 가 venv 생성 → torch CUDA wheel → requirements 를 한 번에 처리한다. torch 를 **먼저** 받아서 PyPI 의 CPU torch 를 받았다가 폐기하는 낭비를 피한다.

```bash
bash scripts/step1_install_base_env.sh   # 기본: cu124 + requirements
source .venv/bin/activate                # 현재 셸에 활성화
bash scripts/step1_check_base_env.sh     # 설치 결과 sanity check (Python/torch/CUDA/패키지)
```

옵션:

```bash
bash scripts/step1_install_base_env.sh --recreate          # 기존 .venv 삭제 후 재설치
bash scripts/step1_install_base_env.sh --cpu               # GPU 없는 환경
bash scripts/step1_install_base_env.sh --cuda-version=126  # cu126 wheel 사용
```

### 3. 인프라 컨테이너 (step2)

두 단계로 나뉜다 — step2_1 은 호스트에 docker 자체를 설치(sudo + 재로그인 필요할 수 있음), step2_2 는 docker 위에 LinkMind 컨테이너 4종을 기동:

**step2_1 — Docker Engine + NVIDIA Container Toolkit 설치** (sudo 필요, 한 번만)

```bash
bash scripts/step2_1_install_docker.sh   # docker-ce + compose v2 + nvidia-container-toolkit
# 설치 직후 docker 그룹이 현재 셸에 적용 안 됨 → 새 셸 (exec su -l "$USER") 또는 newgrp docker
bash scripts/step2_1_check_docker.sh     # docker / compose / nvidia runtime / hello-world 풀체인 검증
```

옵션: `--no-nvidia` (CPU 환경, toolkit skip)

**step2_2 — LinkMind 인프라 컨테이너** (Postgres / Qdrant / Ollama / OpenWebUI 기동)

```bash
bash scripts/step2_2_setup_infra.sh        # docker compose up -d + healthy 대기
bash scripts/step2_2_check_infra.sh        # 4개 서비스 연결성 + 포트 검증
```

옵션:

```bash
bash scripts/step2_2_setup_infra.sh --phase2     # + TEI / MinIO
bash scripts/step2_2_setup_infra.sh --recreate   # 컨테이너 강제 재생성
```

### 4. LLM 런타임 (step3) — Ollama 또는 vLLM

**Ollama** (가벼움, 다양 모델 swap):

```bash
bash scripts/step3_setup_ollama.sh       # 기본 모델 pull
bash scripts/step3_check_ollama.sh       # 컨테이너/API/모델 존재/generate dry run
```

**vLLM** (Phase 2.5 wave-4 부터 default — Ollama 대비 ~30x throughput, qwen2.5:14b
3분 → vllm/Qwen2.5-7B 7초 검증). docker-compose `profile: vllm` 활성화:

```bash
docker compose --env-file env/dev.env -f compose/docker-compose.dev.yml \
    --profile vllm up -d vllm                              # 첫 부팅 — 모델 HF Hub
                                                            # 다운로드 15GB (~10-20분)
# Settings UI 에서 provider=vllm 또는 PUT /settings/llm
curl -s -X PUT http://localhost:8000/settings/llm \
     -H 'content-type: application/json' \
     -d '{"default_llm_provider":"vllm","vllm_model":"Qwen/Qwen2.5-7B-Instruct"}'
```

`env/dev.env.example` 의 `VLLM_MODEL`, `VLLM_GPU_MEM_UTIL` 참조. healthcheck
`start_period: 1800s` (30분 — 첫 다운로드 + cudagraph capture 충분).

### 5. Qdrant 컬렉션 (step4)

bge-m3 모델 첫 로드(약 1.4GB) 후 컬렉션 생성:

```bash
python -m backend.jobs.init_qdrant      # 컬렉션 생성
bash scripts/step4_check_qdrant.sh       # 컬렉션 존재 + vector dim 일치 확인
```

### 6. 한 명령으로 전부 기동 (step5)

```bash
# 넷 다 idempotent 자동 (기존 process 정리 후 재기동)
bash scripts/step5_run_dev.sh             # backend + Streamlit + frontend + telegram
bash scripts/step5_run_dev.sh --status    # pid + 포트 + 최근 로그
bash scripts/step5_run_dev.sh --stop      # 넷 다 종료
bash scripts/step5_run_dev.sh --no-telegram      # telegram 빼고 셋
bash scripts/step5_run_dev.sh --no-frontend-v2   # Next.js 빼고 셋 (Phase 1-2 모드)
bash scripts/step5_run_dev.sh --backend-only
bash scripts/step5_run_dev.sh --frontend-v2-only

# 첫 실행 시 frontend/node_modules 가 없으면 자동으로 npm install (1-2분, 1회만).
# Node 22+ 가 없으면 frontend 만 silent skip.
```

기동되는 서비스:

| 서비스 | URL | 역할 |
|---|---|---|
| FastAPI backend | http://localhost:8000 (`/docs` Swagger) | DB + 검색 + ingest + graph API |
| **Next.js (frontend/)** | **http://localhost:3001** | Graph (3D) · Ingest · Search · Settings 일원화 UI |
| Telegram daemon | (백그라운드) | LinkMind-Inbox 채널 listen + 자동 ingest |

> Streamlit (frontend/) 은 Phase 2.5 wave-3 에서 폐기 — Settings/Ingest/Search 가 frontend 페이지로 마이그레이션됨. 폴더 자체는 회고용으로 남기지만 step5 가 시작 안 함.

확인:

```bash
curl http://localhost:8000/health | jq
curl http://localhost:8000/graph/topics | jq '.nodes | length'
```

### 7. 첫 자료 수집 (URL 한 건)

```bash
python -m backend.ingest.url https://arxiv.org/abs/2106.09685
# 또는 --force 로 기존 hash 있어도 summary/tags 재계산
python -m backend.ingest.url --force https://arxiv.org/abs/2106.09685
```

또는 frontend `Ingest` 탭에서 URL 입력 — host 자동 분류 (youtube / github /
*.pdf / 일반 페이지) + PDF 파일 업로드. force 체크박스로 재계산 가능.

**arxiv / IEEE / DOI URL** 은 자동으로 진짜 논문 제목 추출 (arxiv API hook +
publisher SSR title). 예: `https://arxiv.org/abs/2003.02014` → title=
"Redesigning SLAM for Arbitrary Multi-Camera Systems" (URL 이 아닌).

같은 주제 (같은 arxiv_id / github_repo / doi / yt_id) 의 자료가 여러 modality 로
들어오면 자동으로 한 **topic** 으로 묶임 — `Topics` 탭에서 확인.

### 7.5. D10 wiki — 자료를 wiki 페이지로 자동 합성 + D11 UX 통합 (2026-05-26 ~ 27)

수집한 자료를 의미 단위 wiki 페이지로 자동 분류 + LLM markdown 본문 합성.
일반 chunk-RAG 가 아닌 **karpathy llm_wiki + multi-agent** 패턴.

**신규 ingest 자동 흐름** — 사용자 행동 X. 텔레그램에 URL 던지면:
```
ai_agents → /ingest/url 또는 ingest_telegram_message (in-process)
   ↓
items raw 저장 + 채널 삭제 (즉시)
   ↓
classifier hook (in-process / BackgroundTask):
   - 1:1 fallback wiki 항상 생성 (role='self', body_status='pending') ★ D11 ★
   - Qdrant 의미 검색으로 매칭 wiki 후보 → LLM JSON M:N → wiki_page_items 매핑
   ↓
analysis_worker (backend lifespan):
   - chunks (embedding) + summary (LLM)
   ↓
wiki_writer_worker daemon (lifespan, concurrency 4):
   - WHERE body_status IN ('issues', 'pending') + SKIP LOCKED
   - 자동 body 합성 (vLLM Qwen2.5-7B, 평균 ~3.7s/page) → body_status='completed'
   ↓
사용자가 frontend `/wiki/[slug]` 열면 항상 completed 상태 (클릭 가능)
```

**status 모델 (D11 정리)** — DB `body_status` 3종 + sub-state:

| status | 의미 | UI |
|---|---|---|
| `issues` | 잔여 자료 — 실패 reset / 미처리 | rose, [⚡ 일괄 합성] 버튼 |
| `pending` | 처리 큐 — classifier 가 즉시 마킹 | blue animate-pulse (generating) / amber (queuing) |
| `completed` | 합성 완료 — 페이지 클릭 가능 | emerald, `<Link>` |

sub-state: `body_processing_started_at` (TIMESTAMPTZ) — pending + 5분 안 = generating,
그 외 = queuing. 옛 명명 (empty/stale/ready/generating) 완전 폐기.

**옛 자료 일괄 backfill** (사용자 직접 1회 실행 — ~1일 / 23,792 pages):
```bash
bash scripts/run_wiki_backfill.sh                       # 백그라운드 시작 (nohup)
bash scripts/run_wiki_backfill.sh --tail                # 진행률 실시간
bash scripts/run_wiki_backfill.sh --status              # 요약 + log 5줄
bash scripts/run_wiki_backfill.sh --stop                # graceful (현재 page 끝나고)
bash scripts/run_wiki_backfill.sh --stop-force          # 즉시 SIGKILL
bash scripts/run_wiki_backfill.sh --concurrency 8       # 더 빠르게 (VRAM 여유)
bash scripts/run_wiki_backfill.sh                       # 한 줄 재시작 (옛 거 자동 SIGKILL)
```

성능: concurrency=4 + vLLM continuous batching → page 당 **~3.7초** (sequential
17초 대비 4.6x). 23,792 page 약 24 시간.

**frontend `/wiki` (D11 강화)**:
- list:
  - status tab 4종 — 전체 / ✅ completed / ⏳ issues / ⏱ pending~ETA
  - q text 검색 — wp.title/desc/slug/body + items.title/source_url/user_notes/alt_urls + keywords (7 필드)
  - keyword filter — pill click 으로 같은 키워드 wiki 모음
  - pagination 상하 두 곳 + URL query sync (?page, ?page_size, ?status, ?q, ?keyword)
  - 페이지당 라디오 [10][50][100]
  - **completed 만 클릭** — 진행중/대기중은 중복 처리 방지로 페이지 못 봄
  - issues tab `[⚡ 일괄 합성]` 항상 노출 (0건이면 disabled)
- detail — markdown body + Sources panel + Relationship + KeywordsEditor (✏️ 수정
  토글 후 ✕ 삭제 / + 추가 / autocomplete / ✨ 신규 등록)
- 🔄 재합성 / ✏️ 편집 (markdown textarea) / 🗑 영구 삭제 (2단계 confirm)

**frontend `/ask` (D11 Step 1 신규)** — 대화형 RAG, 2-column:
- 좌: 질문 입력 + 답변 + citation chips + related_wikis links
- 우: 클릭한 wiki detail 즉시 view (별 페이지 이동 X)

API endpoints (10+개):
```
GET    /wiki                          # list (status / q / keyword / pagination)
GET    /wiki/{slug}                   # detail (completed 만 접근 — D11 lazy 제거)
GET    /wiki/{slug}?regenerate=true   # 강제 재합성 (옛 버전은 wiki_page_versions 보존)
PATCH  /wiki/{slug}                   # 본문 수동 편집 (D11)
DELETE /wiki/{slug}                   # 영구 삭제 — CASCADE items + Qdrant (D11)
POST   /wiki/{slug}/regenerate        # 명시적 재합성
POST   /wiki/{slug}/keywords          # add/remove (case-insensitive)
GET    /wiki/_keywords/search?q=...   # autocomplete
GET    /wiki/_meta/stats              # issues/pending/completed/total (D11)
POST   /wiki/_meta/batch_regenerate   # 일괄 재합성 (status='issues' default, D11)
POST   /wiki/search                   # Qdrant body embedding 의미 검색
POST   /wiki/classify                 # item(s) → wiki_pages 자동 분류
```

자세한 설계: [docs/llm_wiki_design.md](docs/llm_wiki_design.md)

### 8. 테스트 (CI 와 로컬 분리)

```bash
bash scripts/tests/total/run_all_local.sh       # 5 카테고리 다 (cpu/embedding/integration/llm/gpu)
bash scripts/tests/total/run_ci_simulation.sh   # CI 가 도는 것만 (push 전 점검)
bash scripts/tests/ci/step1_cpu.sh              # 가장 빠른 default suite (≈4s)
```

자세한 정책은 `CLAUDE.md` §9 (Testing 정책) + `scripts/tests/README.md`.

### 9. (선택) Telegram inbox watcher — multi-channel (2026-05-23~)

여러 텔레그램 채널 (논문/동영상/공부 등 inbox 모음) 의 URL/메모/첨부를 한 watcher 가
통합해 자동 ingest + 채널 자동 정리:

```bash
# https://my.telegram.org 에서 API ID/Hash 발급 후 env/dev.env 에 채우기
# (자세히는 docs/telegram_setup.md)
TELEGRAM_API_ID=...
TELEGRAM_API_HASH=...
TELEGRAM_CHANNELS_CONFIG=config/telegram_channels.yaml  # default
```

채널 list 는 `config/telegram_channels.yaml` 단일 진실 (git commit 가능, 비밀 아님):

```yaml
batch_size: 5                                  # 한 번에 N개씩 sequential 처리 (default 5)
channels:
  - invite: https://t.me/+xxx                  # 필수
    delete_after_ingest: true                  # 필수 (채널별 inbox 패턴 on/off)
    name: LinkMind-Inbox                       # 선택 (비우면 채널 title 자동)
  - invite: https://t.me/+yyy
    delete_after_ingest: false                 # 예: 보존 채널
```

```bash
bash ai_agents/telegram_inbox_watcher.sh                # 첫 실행: SMS 인증
bash ai_agents/telegram_inbox_watcher.sh --daemon       # 백그라운드 daemon
bash ai_agents/telegram_inbox_watcher.sh --restart      # 코드/yaml 변경 후 재기동
bash ai_agents/telegram_inbox_watcher.sh --backfill 50  # 채널별 최근 N개도 처리
tail -f /tmp/telegram-watcher.log                       # 로그
```

**동작**:
- batch sequential — yaml 의 채널을 batch_size 씩 [join → backfill 완주 → 다음].
  Telegram FloodWait 회피 + backend 부하 분산 + progress 가시화.
- FloodWait 자동 대기 — 임계 300초 이하면 sleep 후 재시도.
- channel_id cache (`volumes/telegram/channel_id_cache.json`) — 다음 실행에서
  ImportChatInvite skip, rate limit 영향 없이 즉시 join.
- 이미 join 한 채널은 setup() 의 `get_dialogs()` 로 dialog cache 에 즉시 등록.

### 10. (선택) OpenClaw 설치

OpenClaw 를 frontend agent 로 쓰면 Telegram/Slack 입력을 OpenClaw 가 받아서 LinkMind 로 forward 한다.

```bash
bash scripts/install_openclaw.sh              # 기본: 공식 install.sh (Node 자동 bootstrap)
# bash scripts/install_openclaw.sh --npm      # 팀/CI 환경
# bash scripts/install_openclaw.sh --source   # OpenClaw 자체 수정용
```

자세한 통합은 [docs/openclaw_integration.md](docs/openclaw_integration.md) 참고.

### 10. (선택) Slack 데이터 import

비공개 채널 / DM 까지 받고 싶다면 slackdump 사용. 토큰 / 쿠키 추출 + export 절차는 [docs/slack_setup.md](docs/slack_setup.md) 참고.

Phase C wave-2 (2026-05-19~) 이후 LinkMind 자체 ingest 도 지원:

```bash
# 1) slackdump 로 workspace 전체 export (files=true 첨부 포함)
bash scripts/slack_export.sh                                       # archive/slack_export/full_<ts>/ + latest symlink

# 2) backend uvicorn 의 임베딩 모델이 GPU 점유 중이면 종료 (OOM 회피)
bash scripts/step5_run_dev.sh --stop

# 3) LinkMind 로 backfill — Telegram 패턴 일관 (URL 자동 host 라우팅, thread parent → 자식 caption 전파, 첨부 → ingest_document, mrkdwn entity 정리)
bash scripts/slack_ingest_all.sh                                   # tqdm 진행률 + archive/slack_export/issues/<ts>/manifest.json 자동 보존
bash scripts/slack_ingest_all.sh --channel 가-공부-cuda-programming  # 단일 채널 (디버깅)

# 4) 끝나면 backend/frontend 재기동
bash scripts/step5_run_dev.sh
```

이슈 manifest (DB 에 안 들어간 URL — LinkedIn login wall / 정적 project page / mp4 video 등) 는 `archive/slack_export/issues/<timestamp>/manifest.json` 에 자동 보존 — 후속 wave 에서 별도 패턴별 재처리.

---

## 디렉토리 구조

```
LinkMind/
├─ backend/                 # FastAPI 백엔드
│  ├─ api/                  # /health, /ingest, /search, /ask routers
│  ├─ config.py             # pydantic-settings 환경설정
│  ├─ db/                   # Postgres 연결 + repository + schema.sql
│  ├─ embedding/            # EmbeddingProvider (local / tei / ollama)
│  ├─ ingest/               # 소스별 ingester (url, pdf, slack, ...)
│  ├─ llm/                  # LLMProvider (openai / claude / ollama)
│  ├─ rag/                  # (Phase 2) retrieval/answering 분리
│  ├─ schemas/              # Pydantic 요청·응답 모델
│  ├─ storage/              # 파일 storage (local → minio)
│  ├─ utils/                # chunking, hashing
│  └─ main.py               # FastAPI 진입점
├─ frontend/                # Streamlit MVP UI
├─ compose/                 # docker-compose.dev.yml (+ prod, phase2 profile)
├─ docker/                  # 서비스별 Dockerfile / 설정 (필요 시)
├─ env/                     # dev.env (gitignored) / dev.env.example
├─ scripts/                 # stepN_setup_*.sh / stepN_check_*.sh + install_openclaw.sh, ollama_pull.sh, ...
├─ docs/                    # openclaw_integration.md, training_data_design.md
├─ archive/                 # raw 자료 저장 (gitignored)
├─ volumes/                 # 컨테이너 영속 볼륨 (gitignored)
├─ external/openclaw/       # OpenClaw 참조용 clone (gitignored)
└─ tests/                   # pytest
```

## 핵심 설계 원칙

### Raw-first / Provenance / Idempotent / Versioned / Loss-less

모든 ingestion 은 **원본을 먼저 저장**한다. 분석/임베딩은 그 후. AI 분석 결과는 모델/프롬프트 버전과 함께 저장되어, 더 좋은 모델이 나오면 재분석만 하면 된다. **sVLL 학습 시 raw 데이터가 손실되어 있으면 안 됨**.

자세한 내용: [docs/training_data_design.md](docs/training_data_design.md)

### 단일 self-contained 시스템 (Phase 2.5+, 2026-05-18)

LinkMind 는 backend (`backend/`) + multi-channel gateway (`ai_agents/`) + Streamlit MVP (`frontend/`) + Next.js graph UI (`frontend/`, Phase 2.5+) 를 한 저장소에서 같이 유지. self-host 한 방으로 다 따라온다.

외부 프로젝트 (openclaw, hermes-agent, hermes-webui) 는 `external/{name}/` 의 gitignored clone — **벤치마킹 참조 전용**. 셋 다 MIT 라이센스라 부분 코드 vendor 도 가능 (attribution 보존). 다만 통째 fork 대신 idea/UX 패턴 차용 후 자체 구현이 일반적.

자세한 내용: [docs/agent_architecture.md](docs/agent_architecture.md)

### 빠른 MVP 우선

과도한 추상화 / 디자인 패턴 / generic architecture 는 의도적으로 피함. 단, 재배포 / 서버 이전 / SaaS 화 가 가능하도록 환경변수·볼륨·compose 구조는 처음부터 분리.

## Phase 별 로드맵

| Phase | 핵심 내용 | 상태 |
|---|---|---|
| 1   | Postgres + Qdrant + URL ingest + 임베딩 + Semantic Search + RAG | ✅ 완료 |
| 2 first wave   | 4종 ingester (url/youtube/github/pdf), Settings UI, DB-backed runtime, 한국어 prompt v3, `/files/{hash}` | ✅ 완료 |
| 2 second wave  | `ingest --force`, PDF figure 추출, abstract regex 보강, YouTube 썸네일 attachments | ✅ 완료 |
| **2.5 (Topic 그룹핑)** | `topics`+`item_topics` 스키마, external_ids extractor, 자동 매핑, Topics UI, description 자동 생성 | ✅ 완료 |
| **2.5 wave-2** | arxiv API seed (title/abstract 자동), 검색 결과의 multi-modal 인라인 노출, manual link autocomplete | ✅ 완료 |
| **C wave-1 (Telegram inbox)** | Telethon daemon, 채널 메시지 → 자동 ingest + URL 라우팅 + topic 매핑, 처리 후 채널에서 자동 삭제 (inbox 패턴) | ✅ 완료 (실 환경 검증) |
| **리팩토링** | `scripts/` 는 .sh 만 / `backend/jobs/` batch python / `ai_agents/` client agent — 5 카테고리 135 tests | ✅ 완료 |
| **2.5 wave-3 (단일 self-contained, 2026-05-18)** | (1) §3 재정의 + §14 신규 (AGPL+Privacy+SaaS path) + docs/agent_architecture.md (2) `ai_agents/base.py` ChannelAgent ABC + telegram refactor (3) items 스키마 user_notes/is_read/read_at + GET/PATCH /items/{id} + LLM 키워드 추출 BackgroundTask (4) `backend/ingest/document/` 통합 추출 (PDF + DOCX/PPTX/TXT/MD, 한국어 cp949) (5) 텔레그램 첨부 자동 ingest + caption → user_notes 자동 (6) VOLUMES_ROOT env (compose bind mount root 설정 가능) (7) graph backend `/graph/*` — cytoscape.js 호환 JSON | ✅ 완료 |
| **2.5 wave-4 (categories 레이어 + Union 그래프 + Theme, 2026-05-18~19)** | (1) **fallback topic** — external_id 없는 url 도 자체 topic 자동 (193 backfill, Houdini 같은 키워드도 카테고리로 살아남음) (2) **categories 스키마** + auto_link_categories job (61 카테고리 + 796 link, items.tags 빈도 ≥3) (3) **3-tier graph endpoint** — `/graph/categories`·`/graph/category/{slug}`·`/graph/topic/{uuid}` (4) **caption append 정책** — 모든 ingest 에 caption 파라미터, `append_item_user_notes` idempotent + timestamp 구분자 (5) **vLLM 가동** — Ollama → vLLM (qwen2.5:14b 3분 → vllm/Qwen2.5-7B 7초, 30x), `default_llm_provider: vllm` (6) **frontend 대개편** — i18n (한/EN 토글), 3-tier sidebar 트리 (cat ▸ topic ▸ item), 색상 그룹화 (Articles=녹/Video=빨/Code=보/Web=파/Note=시안), 양방향 highlight (`relatedIds`), Union 그래프 (`mergeGraph` 유니온 스테이션 hub-spoke), NodeDetails 통합 패널, ItemDetails 자동 expand, ThemeToggle (☀️/🌙/🖥 system + localStorage), Legend 그룹별 + 선택 상태 안내, navigation history (← 이전 / ← 전체) (7) 6개 텔레그램 fail 메시지 자동 처리 (url-only fetch_error key, youtube /live/, github owner-only fallback) (8) 181 topics title cleanup (cross-modal 차용 버그) | ✅ 완료 |
| **C wave-2 Slack 일회성 backfill (2026-05-19 ~ 23)** | 시한 리스크 (사용자 구독 해제 임박) → 미리 확보. **모듈/CLI/테스트/스크립트**: `backend/ingest/slack/{export_parser,__init__,__main__}.py` (Telegram 패턴 미러: mrkdwn entity 정리, blocks/raw URL 추출, thread parent → 자식 caption 전파, 시스템 메시지 skip), `tests/test_slack_parser.py` 46 케이스, `scripts/slack_ingest_all.sh`. 부수 fix: `_classify_url` 의 `/pdf/` path 인식 (arxiv pdf URL 라우팅 버그). **전체 14241 메시지 ingest 완료** (2026-05-23). 결과: `archive/slack_export/issues/20260519-220427/manifest.json` (953 issues / 6.7% 실패율) — placeholder 633 + exception 320. fix 불가 (~40%): YouTube 영상 삭제 174 / LinkedIn login wall 141 / Facebook 65 / DNS 실패 20. 회수 가능 (~60건+): URL protocol 없음 9 / channel URL 14 / openaccess.thecvf 18 / medium Wayback 88 등. | ✅ 완료 |
| **2.5 wave-5 인프라 (D13 vLLM-embed, 2026-05-23)** | **OOM 근본 fix**. bge-m3 가 프로세스마다 GPU 별도 로드 (uvicorn 4.4 GB + watcher 3.8 GB + vLLM 18.8 GB ≈ 27 GB > 24 GB). vLLM 통일 (TEI 안 씀): compose 에 `vllm-embed` (`--runner pooling`) 추가, `backend/embedding/vllm_embed.py` 신규, `factory.py` env switch. GPU: vllm-llm 18.5 + vllm-embed 2.0 = 20.5 / 24 GB. analysis_worker transaction 버그 fix + backfill_summary chunks 보강. | ✅ 완료 (commit 46c36eb) |
| **Telegram multi-channel via yaml (2026-05-23)** | 단일 LinkMind-Inbox → 15개 채널 통합 inbox. `config/telegram_channels.yaml` 단일 진실 (필수: invite/delete_after_ingest, 선택: name). watcher: FloodWait 자동 대기, channel_id cache + dialog cache (rate limit 회피). `ai_agents/telegram_channels.py` 신규 + 17 테스트. 옛 single fallback 완전 제거. | ✅ 완료 (commit 7d20119) |
| **2.5 wave-5 보강 (2026-05-25)** | (1) **batch 구조 제거** — GPU VRAM 한계로 어차피 직렬 ingest, batch 효익 X. yaml 순서대로 한 채널씩 [resolve → backfill → 다음] (`[N/M]` 진척 로그). (2) **`ai_agents/check_telegram_invites.py`** — invite 검증 + yaml/cache 자동 정리 (ALIVE/NOT-MEMBER/DEAD-HASH 분류 + .bak.<ts> 백업). 17 테스트. (3) **`step5_run_dev.sh` 통합** — watcher 시작 직전 check 자동 실행 (`--skip-check` 옵션). 사용자가 텔레그램에서 leave/추방한 채널 자동 정리. 실제 검증: yaml 14→6 / cache 9→3. (4) **Graph "node not found" 근본 fix** — `backend/api/graph.py` 3 endpoint 가 `list_topics(limit=500)` 으로 fetch 후 dict lookup → DB 의 23,631 topic 중 limit 밖은 dangling. `list_topics_by_ids(ids)` 신규로 정확 fetch. (5) **URL ingest OG meta fallback** — 본문 추출 실패 시 og:title/description/image 로 raw body 합성 (LinkedIn/Facebook 같은 SNS 도 카드 데이터 보존). abstract cutoff 200→100자. 17 테스트. (6) **YouTube channel handle + oEmbed fallback** — `/@username` channel URL 인식 (`channel` kind, url ingest fallback). yt-dlp IP 차단 시 (위장: "video not available") oEmbed API → title + author + thumbnail + LLM summary/tags. 차단됐던 영상도 정상 ingest 검증. 7 테스트. (7) **fallback topic 테스트 갱신** (wave-3 동작 반영). **327 passed / 0 회귀**. | ✅ 완료 |
| **D12 placeholder/실패 자료 정리 (2026-05-25)** | §2 raw-first 의 확장 — 본문 fetch 실패해도 raw + URL + provenance 무조건 DB 보존 + 사용자 수동 보강 UI. **Wave-1** (commit 39a82d1): `backend/jobs/mark_fetch_failed.py` 자동 분류 (2594건 마킹: image_no_ocr 1751 / extraction_failed 806 / binary 36 / short 1). `GET /items` list + facets drilldown + POST `/items/{id}/notes` (user_notes append) + `/categories/{slug}` (manual link). `frontend/app/cleanup/` — FilterSidebar + ItemCard (이미지 grid) + ActionPanel (kind별 hint). 32 단위 테스트. **Wave-2** (commit aba9f65): Slack manifest 재처리 — `backend/db/repository.merge_source_metadata` + Slack URL 분기 slack provenance 보강 + `backend/jobs/ingest_slack_manifest.py` (placeholder 633 메타 보강 + exception 320 wave-5/D13 fix 흐름 재시도). 결과: 915 / 953 (96%) DB 등록, unresolved 38건만 진짜 손실 (YouTube 영상 삭제 37 + 깨진 URL 1). 14 테스트. **Slack 정리**: `archive/slack_export/` 628MB 삭제 (DB items 1307 + volumes/archive 4.7GB 보존 검증). **Wave-3** (commit 269b3b5): `DELETE /items/{id}` + ActionPanel 의 2단계 confirm 영구 삭제 UI. 진짜 사라진 자료 (영상 삭제 / 도메인 죽음 / HTTP 429 영구 차단) 정리. Qdrant points + Postgres CASCADE (chunks / attachments / item_topics 자동 삭제). §11 Privacy §4 (삭제 권리) 부합. | ✅ 완료 |
| **D10 llm_wiki 아키텍처 wave-1+2 (2026-05-26)** | karpathy llm_wiki + multi-agent (physics-intern state-centric) + YAML prompt (ml-intern) 차용. **wave-1**: schema 4 테이블 + 4 agent (classifier/retriever/writer + critic stub) + wiki API 8 endpoints + Qdrant body embedding 검색 + frontend rename (frontend_v2 → frontend, 44 refs) + `/wiki/` list+detail+KeywordsEditor view/edit + backfill 23,852 pages. **wave-2c**: analysis_worker classifier hook (텔레그램 신규 ingest 자동 wiki 분류). **wave-2d**: keywords 진화 (schema TEXT[] + writer prompt 자동 추출 + 3 API + KeywordsEditor view/edit + matching wiki filter + ✨ 신규 등록). **arxiv URL hook** (`backend/ingest/arxiv/`, 신규 arxiv/IEEE/DOI 진짜 제목). **daemon 분리**: `wiki_writer_worker` (lifespan, stale 만, 신규 ingest 자동) + `wiki_writer_batch` CLI (empty+stale, 사용자 직접). **성능 4.6x**: vLLM prefix-caching + max-num-batched-tokens 16384 + max_tokens 1024 + concurrent N=4 → 17초/page → 3.7초/page. ETA 4.7일 → ~1일. | ✅ 완료 |
| **D11 wiki 중심 UX 통합 (2026-05-27, 26 commits)** | D10 위에 사용자 mental model 정리. **(1) wiki 중심 통합** — `frontend/app/cleanup/` + `frontend/app/search/` 디렉토리 폐기, Header nav 단순화 (graph / ingest / Ask / wiki / settings). **(2) status 모델 통일** — DB `body_status` 3종 (issues / pending / completed) + `body_processing_started_at` sub-state (5분 안 = generating blue animate-pulse, 그 외 = queuing amber). 옛 명명 (empty/stale/ready/generating) 완전 폐기. **(3) wiki list UX 강화** — 4 status tab + pagination 상하 + 페이지당 라디오 (10/50/100) + ETA (시간+분, 1초/page) + URL query sync (?page/?page_size/?status/?q/?keyword) + **completed 만 클릭** (중복 처리 방지) + 정렬 (전체: status 그룹 + 알파벳 / pending: generating 우선) + 일괄 합성 버튼 항상 노출. **(4) classifier 1:1 fallback wiki** — `role='self'` wiki 항상 INSERT (사용자 자료 1순위). **(5) daemon = batch (concurrency 4 통일)** — sequential → asyncio.gather, ON CONFLICT DO NOTHING (race-free). **(6) GET /wiki/{slug} lazy 합성 제거** — `?regenerate=true` 만 LLM. **(7) `/ask` 신규 페이지 (Step 1)** — 2-column 대화형 RAG (좌 chat + citation + related_wikis / 우 wiki detail). **(8) 텔레그램 watcher classifier hook** — `ingest_telegram_message` 끝에 fire-and-forget `asyncio.create_task` (in-process 흐름). **(9) bare domain URL regex** (unite.ai 자동 https://) + **dedup URL 보존** (`source_metadata.alt_urls` 누적). **(10) wiki 검색 7 필드** — title/desc/slug/body + items.title/source_url/user_notes + alt_urls + keywords. **(11) step5 turbopack 캐시 자동화** + `--clean-cache`. 383/383 cpu PASS. | ✅ 완료 |
| **D10.5 재정의 — graph ↔ wiki ↔ 카테고리 데이터 흐름 통합 (3 세션)** | 사용자 통찰 (2026-05-27): 자료 클릭 시 wiki 링크 안 보이고, 카테고리 ↔ wiki keywords 별 시스템. 사용자 결정: (1) wiki keywords 로 통합 (`categories` 테이블 + `auto_link_categories` job + 카테고리 트리 폐기), (2) 그래프 자료 클릭 → 우측 ItemDetails 패널 안에 wiki body inline (별 페이지 X). **세션 A**: `GET /items/{id}` 에 `wiki_page_items` 조인 + ItemDetails 에 user_notes textarea + wiki body inline (WikiBody 재사용) + 여러 wiki 면 탭 + keywords pill. **세션 B**: 66 categories → 대표 keyword 매핑 + `/graph/categories` 재설계 + TopicsTree `keyword ▸ wiki ▸ item` + 옛 categories 시스템 삭제. **세션 C (선택)**: `/wiki` 검색 + graph + `/ask` 페이지 일원화. | 🚧 다음 (세션 A 부터) |
| 후속 backlog | D10 wave-3 critic agent / /ask Step 2/3 (대화 history + filing-back) / D12-4 wiki 기반 자료 보강 / D8 cross-modality / D10 lint / dataset exporter (Phase 3 후반) | future |
| D9/D11/D8 | D9 arxiv title 재시드 (이미 신규 ingest hook 으로 자동) → D11 카테고리 UI 편집 (keywords API 패턴 재사용) → D8 cross-modality matching (wiki body 안 자연) | |
| 2 후반 (AI 카테고리/feedback/dataset exporter) | AI 카테고리 강화, feedback 테이블, dataset exporter (JSONL) | |
| 3 | 이미지/OCR/멀티모달 RAG, MinIO object storage, `ai_agents/` 채널 확장 (Slack/WhatsApp/Discord), 자가학습 (auto prompt/ingester 개선) | |
| 4 | **sVLL LoRA 파인튜닝** (LLaMA-Factory + Qwen2-VL), vLLM 서빙 — self-host 또는 hosted enterprise tier 옵션 | |
| 5 | Continuous training loop, on-prem AI 엔진 완성 | |
| 6 (선택) | OSS (AGPL v3) 공개 → hosted SaaS (Next.js + Auth.js + Stripe, BYOK, multi-tenant) | |

자세한 backlog 와 phase 별 완료/미구현 항목 — `docs/features_backlog.md` + `CLAUDE.md §13` 참고.

### 다음 세션 진입 순서 (2026-05-27 갱신, D10.5 재정의 후)

**완료까지의 흐름**:
- ✅ D10 wave-1+2 (2026-05-26) — schema + 4 agent + wiki API + backfill 시작
- ✅ D11 (2026-05-27, 26 commits) — UX 통합 + status 3종 + classifier 1:1 fallback + /ask Step 1

**backfill 잔여는 D11 daemon=batch (concurrency 4) 가 자동 처리** —
`curl -s http://localhost:8000/wiki/_meta/stats | jq` 로 확인.

### D10.5 재정의 (2026-05-27 사용자 결정) — 3 세션 분리

**사용자 통찰**: "그래프 자료 클릭해도 wiki 페이지 링크 안 보이고, 카테고리 ↔ wiki
keywords 연동도 안 됨." 진단 결과 — DB 의 `wiki_page_items` 데이터는 있지만 UI 에서
끊김 + `categories` 와 `wiki_pages.keywords` 가 별 시스템.

**사용자 결정**:
- **결정 1**: wiki keywords 로 통합 — 옛 `categories` 테이블 + `auto_link_categories`
  job + 그래프 좌측 카테고리 트리 모두 폐기. wiki_pages.keywords 만 남기고 트리도
  keyword 기반 재구성.
- **결정 2**: 그래프 자료 클릭 → 우측 ItemDetails 패널 안에 wiki body inline
  (`/ask` 패턴). 별 페이지 이동 X.

**3 세션 분리**:

| 세션 | 작업 | 단계 |
|---|---|---|
| **A (다음)** | **graph 우측 패널에 wiki inline** (1 세션) | A1 `GET /items/{id}` 에 `wiki_page_items` 조인 / A2 ItemDetails 에 user_notes textarea / A3 ItemDetails 에 wiki body inline (WikiBody 재사용) / A4 wiki 여러 개면 탭 / A5 keywords pill 표시 |
| **B** | **categories → keywords 전환** (1-2 세션) | B1 66 categories → 대표 keyword 매핑 / B2 `/graph/categories` 재설계 / B3 TopicsTree `keyword ▸ wiki ▸ item` / B4 옛 categories 시스템 삭제 |
| **C** | **페이지 일원화** (1 세션, 선택) | `/wiki` 검색 + graph + `/ask` UX 일관성 |

**왜 A 먼저?** 사용자가 직접 써보고 mental model 검증 후 B/C 의사결정.

### D10.5 후 backlog

| # | 작업 | 규모 |
|---|---|---|
| 1 | D10 wave-3 — critic agent (citation 검증 + contradiction flag + writer 후처리) | 1-2 세션 |
| 2 | /ask Step 2 (대화 history) → Step 3 (filing-back: 답변 → wiki 적립) | 1-2 세션 |
| 3 | D12-4 wiki 기반 자료 보강 마킹 | 중 |
| 4 | D8 cross-modality matching | 중 |
| 5 | D10 lint job (모순/stale/orphan 정기 점검) | 중 |
| 6 | dataset exporter (Phase 3 후반, JSONL → Phase 4 LoRA 학습 입력) | 큼 |

### status 모델 (인지 필수)

| status | 의미 | UI |
|---|---|---|
| `issues` | 잔여 — 실패 / 미처리 | rose + [⚡ 일괄 합성] |
| `pending` | 처리 큐 | blue (generating, 5분 안) / amber (queuing) |
| `completed` | 합성 완료 — 클릭 가능 | emerald `<Link>` |

sub-state: `body_processing_started_at` (TIMESTAMPTZ).

### 제어 명령

```bash
bash scripts/step5_run_dev.sh                       # 전체 가동
bash scripts/step5_run_dev.sh --stop                # 정지
curl -s http://localhost:8000/wiki/_meta/stats | jq # wiki 상태 확인
```

**장기 로드맵**: Phase 3 후반 (feedback 테이블 + dataset exporter JSONL) → Phase 4
(sVLL LoRA fine-tune, Qwen2-VL + 사용자 본인 LoRA adapter) → Phase 5 (continuous training).

## 라이센스

**AGPL v3** (OSS 공개 시점에 LICENSE 추가 — Phase 6-B, 6+개월 후 예정). self-host 무제한 자유, 변형해서 SaaS 로 재판매 시 변경 사항 공개 의무. Plausible/Cal.com/n8n 채택 모델. 자세히는 [CLAUDE.md §14](CLAUDE.md).

`external/{openclaw,hermes-agent,hermes-webui}/` 의 참조 코드는 셋 다 **MIT** — AGPL v3 와 호환. 부분 코드 vendor 시 LICENSE/copyright notice 보존 필수.

## 기여 / 코드 스타일

- Python typing 사용, Pydantic schema, async/await 우선
- FastAPI router 구조, 함수 단위 분리
- 주석은 한국어 OK, 충분히 작성
- 과도한 OOP / 디자인 패턴 지양
