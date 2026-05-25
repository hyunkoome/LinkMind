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
                  FastAPI + Streamlit (MVP)
                  + Next.js graph UI (Phase 2.5+)
                           │
                           ▼
        /ingest  /search  /ask  /graph  /items  /topics
                           │
                           ▼
           (Phase 2+) dataset export
                           │
                           ▼
        sVLL 파인튜닝 (LoRA / QLoRA) — 사용자 본인 데이터로 본인 모델만
                           │
                           ▼
         vLLM / Ollama 로 온프레미스 서빙
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
bash scripts/step5_run_dev.sh             # backend + Streamlit + frontend_v2 + telegram
bash scripts/step5_run_dev.sh --status    # pid + 포트 + 최근 로그
bash scripts/step5_run_dev.sh --stop      # 넷 다 종료
bash scripts/step5_run_dev.sh --no-telegram      # telegram 빼고 셋
bash scripts/step5_run_dev.sh --no-frontend-v2   # Next.js 빼고 셋 (Phase 1-2 모드)
bash scripts/step5_run_dev.sh --backend-only
bash scripts/step5_run_dev.sh --frontend-v2-only

# 첫 실행 시 frontend_v2/node_modules 가 없으면 자동으로 npm install (1-2분, 1회만).
# Node 22+ 가 없으면 frontend_v2 만 silent skip.
```

기동되는 서비스:

| 서비스 | URL | 역할 |
|---|---|---|
| FastAPI backend | http://localhost:8000 (`/docs` Swagger) | DB + 검색 + ingest + graph API |
| **Next.js (frontend_v2/)** | **http://localhost:3001** | Graph (3D) · Ingest · Search · Settings 일원화 UI |
| Telegram daemon | (백그라운드) | LinkMind-Inbox 채널 listen + 자동 ingest |

> Streamlit (frontend/) 은 Phase 2.5 wave-3 에서 폐기 — Settings/Ingest/Search 가 frontend_v2 페이지로 마이그레이션됨. 폴더 자체는 회고용으로 남기지만 step5 가 시작 안 함.

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

또는 Streamlit `Ingest` 탭에서 URL 입력 — host 자동 분류 (youtube / github /
*.pdf / 일반 페이지) + PDF 파일 업로드. force 체크박스로 재계산 가능.

같은 주제 (같은 arxiv_id / github_repo / doi / yt_id) 의 자료가 여러 modality 로
들어오면 자동으로 한 **topic** 으로 묶임 — `Topics` 탭에서 확인.

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

LinkMind 는 backend (`backend/`) + multi-channel gateway (`ai_agents/`) + Streamlit MVP (`frontend/`) + Next.js graph UI (`frontend_v2/`, Phase 2.5+) 를 한 저장소에서 같이 유지. self-host 한 방으로 다 따라온다.

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
| **D12 placeholder/실패 자료 정리 (2026-05-25)** | §2 raw-first 의 확장 — 본문 fetch 실패해도 raw + URL + provenance 무조건 DB 보존 + 사용자 수동 보강 UI. **Wave-1** (commit 39a82d1): `backend/jobs/mark_fetch_failed.py` 자동 분류 (2594건 마킹: image_no_ocr 1751 / extraction_failed 806 / binary 36 / short 1). `GET /items` list + facets drilldown + POST `/items/{id}/notes` (user_notes append) + `/categories/{slug}` (manual link). `frontend_v2/app/cleanup/` — FilterSidebar + ItemCard (이미지 grid) + ActionPanel (kind별 hint). 32 단위 테스트. **Wave-2** (commit aba9f65): Slack manifest 재처리 — `backend/db/repository.merge_source_metadata` + Slack URL 분기 slack provenance 보강 + `backend/jobs/ingest_slack_manifest.py` (placeholder 633 메타 보강 + exception 320 wave-5/D13 fix 흐름 재시도). 결과: 915 / 953 (96%) DB 등록, unresolved 38건만 진짜 손실 (YouTube 영상 삭제 37 + 깨진 URL 1). 14 테스트. **Slack 정리**: `archive/slack_export/` 628MB 삭제 (DB items 1307 + volumes/archive 4.7GB 보존 검증). **Wave-3** (commit 269b3b5): `DELETE /items/{id}` + ActionPanel 의 2단계 confirm 영구 삭제 UI. 진짜 사라진 자료 (영상 삭제 / 도메인 죽음 / HTTP 429 영구 차단) 정리. Qdrant points + Postgres CASCADE (chunks / attachments / item_topics 자동 삭제). §11 Privacy §4 (삭제 권리) 부합. | ✅ 완료 |
| **D10 llm_wiki 아키텍처 (큰 그림, 여러 세션)** | karpathy llm_wiki + vlm_wiki + multi-agent + 자가학습. 일반 RAG 아닌 **topic = wiki 페이지** 단위. `external/karpathy/llm_wiki/` 분석 → `docs/llm_wiki_design.md` → `backend/agents/` (retriever/writer/critic) → `/wiki/{slug}`. cleanup 페이지에서 사용자 user_notes 로 보강한 자료도 wiki 페이지 합성 시 중요 신호로 활용. | 🚧 다음 |
| D9/D11/D8 | D9 arxiv title 재시드 → D11 카테고리 UI 편집 → D8 cross-modality matching (wiki 모델 안에서 흡수) | |
| 2 후반 (AI 카테고리/feedback/dataset exporter) | AI 카테고리 강화, feedback 테이블, dataset exporter (JSONL) | |
| 3 | 이미지/OCR/멀티모달 RAG, MinIO object storage, `ai_agents/` 채널 확장 (Slack/WhatsApp/Discord), 자가학습 (auto prompt/ingester 개선) | |
| 4 | **sVLL LoRA 파인튜닝** (LLaMA-Factory + Qwen2-VL), vLLM 서빙 — self-host 또는 hosted enterprise tier 옵션 | |
| 5 | Continuous training loop, on-prem AI 엔진 완성 | |
| 6 (선택) | OSS (AGPL v3) 공개 → hosted SaaS (Next.js + Auth.js + Stripe, BYOK, multi-tenant) | |

자세한 backlog 와 phase 별 완료/미구현 항목 — `docs/features_backlog.md` + `CLAUDE.md §13` 참고.

### 다음 세션 진입 순서 (2026-05-25 마감 갱신)

> **오늘 한 일 (2026-05-25)**: wave-5 보강 (commit 6294c3f) + D12 wave-1 (commit
> 39a82d1) + D12 wave-2 (commit aba9f65) + Slack archive 삭제 + D12 wave-3 영구
> 삭제 (commit 269b3b5) + 문서 정리 (commit 36cb6a0, 1affb83, 1924829). **7 commit +
> push.** Slack 데이터는 모두 DB + volumes/archive 에 영구 보존 (1,307 items +
> 4.7GB raw). 다음 세션은 재가동 + D10 llm_wiki 시작.
> 자세한 재개 가이드는 memory `project_next_session_entrypoint`.

**Todo list (다음 세션 시작 시)** — 2026-05-25 사용자 결정 반영:

| # | 작업 | 상태 |
|---|---|---|
| 1 | backend + frontend + watcher 재가동 (`bash scripts/step5_run_dev.sh`) | pending |
| 2 | **D10 llm_wiki 첫 세션 (최우선)** — `external/karpathy/llm_wiki/` 분석 + `docs/llm_wiki_design.md` + `backend/agents/` 4종 (classifier + retriever + writer + critic) 설계. **agent 가 모든 자료 (placeholder 포함) 자동 wiki 분류 → wave-4 휴리스틱 categories 진화** | pending |
| 3 | (D10 안정화 후) `ItemDetails.tsx` 에 user_notes append textarea 통합 — 모든 viewer 공통 1급 기능 | pending |

**사용자 수동 cleanup 작업은 미루기** — D10 agent 가 자동 분류하면 보강 우선순위 명확해짐.
사용자 user_notes 입력은 **모든 viewer 공통 기능** (cleanup 페이지 국한 X) 으로 분리.

**검증 명령 (다음 세션 시작 직후)**:
```bash
nvidia-smi                                                    # driver 정상
docker ps | grep -E "vllm|postgres|qdrant|ollama"           # 인프라 컨테이너
bash scripts/step5_run_dev.sh                                 # check + watcher + backend + frontend
tail -f /tmp/telegram-watcher.log                             # watcher 진행
# 기대 로그: "channel_id cache: N entries", "[1/M] 채널 처리 시작:", "채널 등록:", "listening… M 채널 동시"
```

**Telegram 채널 추가 / 옛 실패 메시지 재ingest**:
```bash
# 새 invite 추가 — yaml 라인 추가 후
bash scripts/step5_run_dev.sh                  # check 가 자동 검증 + watcher join

# 특정 URL 강제 재시도 (channel handle / IP 차단됐던 영상 모두 fallback 동작)
curl -X POST http://localhost:8000/ingest/youtube -d '{"url":"...","force":true}'
```

**우선순위 흐름**:
```
✅ D12 wave-1/2/3 (2026-05-25 완료) — placeholder UI + Slack manifest 재처리 + 영구 삭제
   - wave-1: cleanup 페이지 + mark_fetch_failed 자동 분류 (2594건)
   - wave-2: Slack manifest 953건 재처리 (915 DB 등록 = 96%, unresolved 38건만 손실)
   - wave-3: DELETE /items/{id} + 2단계 confirm UI (영상 삭제 등 진짜 사라진 자료 정리)
   - Slack 첨부 198개 SHA-256 dedup 영구 보존 검증
   │
   ▼
🚧 [다음 — 최우선] D10 — llm_wiki 아키텍처 (큰 그림, 여러 세션)
   - external/karpathy/llm_wiki/ 분석 → docs/llm_wiki_design.md
   - backend/agents/ 4종 (classifier + retriever + writer + critic) + /wiki/{slug} prototype
   - **agent 가 모든 자료 (placeholder 포함) 자동 wiki 분류** → wave-4 휴리스틱 categories 진화
   - vLLM base 모델 inference 만 (학습 X — 학습은 Phase 4)
   - [[project-llm-wiki-arch]] memory 참조
   │
   ▼
☐  ItemDetails 의 user_notes append textarea 통합 (모든 viewer 공통, D10 안정화 후)
   │
   ▼
☐  D12-3 — cleanup 페이지 진화 (wiki 단위 filter + agent 마킹) — D10 후
   │
   ▼
☐  D9 (arxiv title 재시드) → D11 (카테고리 UI) → D8 (cross-modality matching, wiki 안 흡수)
   │
   ▼
[Phase 3 후반] feedback 테이블 + dataset exporter (JSONL, LLaMA-Factory 포맷)
   │
   ▼
[Phase 4] sVLL LoRA fine-tune — PyTorch + LLaMA-Factory + Qwen2-VL 7B
   - 사용자 본인 데이터로 본인 LoRA adapter (~수 MB, base 공유)
   - 별 conda env `linkmind-train`. vLLM 으로 base + adapter 서빙.
   │
   ▼
[Phase 5] Continuous training loop — 자가학습 (feedback → 재학습 → 배포 → feedback)
```

## 라이센스

**AGPL v3** (OSS 공개 시점에 LICENSE 추가 — Phase 6-B, 6+개월 후 예정). self-host 무제한 자유, 변형해서 SaaS 로 재판매 시 변경 사항 공개 의무. Plausible/Cal.com/n8n 채택 모델. 자세히는 [CLAUDE.md §14](CLAUDE.md).

`external/{openclaw,hermes-agent,hermes-webui}/` 의 참조 코드는 셋 다 **MIT** — AGPL v3 와 호환. 부분 코드 vendor 시 LICENSE/copyright notice 보존 필수.

## 기여 / 코드 스타일

- Python typing 사용, Pydantic schema, async/await 우선
- FastAPI router 구조, 함수 단위 분리
- 주석은 한국어 OK, 충분히 작성
- 과도한 OOP / 디자인 패턴 지양
