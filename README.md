# LinkMind

> **개인 데이터로 sVLL(small VLM/LLM) 을 학습시켜 온프레미스 AI 엔진을 만드는 것이 최종 목표**.
> LinkMind 는 그 학습 데이터를 raw-first 원칙으로 수집·구조화하는 backend knowledge OS.

```
[Sources]  Slack export · Telegram · URL · PDF · GitHub · Arxiv · YouTube · Image
              │
              ▼
         [LinkMind backend]                  [OpenClaw (외부 agent)]
              │                                    │
   ┌──────────┼──────────┐                         ▼
   ▼          ▼          ▼               사용자 ↔ Telegram/Slack/...
Postgres   Qdrant    Storage             OpenClaw extension 이
(raw +     (vectors) (files,             LinkMind HTTP API 호출
 metadata)            assets)
              │
              ▼
         FastAPI + Streamlit
              │
              ▼
     /ingest  /search  /ask
              │
              ▼
       (Phase 2+) dataset export
              │
              ▼
   sVLL 파인튜닝 (LoRA / QLoRA)
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

### 4. Ollama 모델 (step3)

env/dev.env 의 `OLLAMA_MODEL` 을 컨테이너로 pull + 동작 검증:

```bash
bash scripts/step3_setup_ollama.sh       # 기본 모델 pull
bash scripts/step3_check_ollama.sh       # 컨테이너/API/모델 존재/generate dry run
```

### 5. Qdrant 컬렉션 (step4)

bge-m3 모델 첫 로드(약 1.4GB) 후 컬렉션 생성:

```bash
python -m backend.jobs.init_qdrant      # 컬렉션 생성
bash scripts/step4_check_qdrant.sh       # 컬렉션 존재 + vector dim 일치 확인
```

### 6. 백엔드 + Streamlit 동시 기동 (step5)

```bash
# 한 셸 — backend (FastAPI :8000) + frontend (Streamlit :8501) 동시 백그라운드
bash scripts/step5_run_dev.sh             # log: /tmp/linkmind-*.log
bash scripts/step5_run_dev.sh --status    # pid + 포트 + 최근 로그
bash scripts/step5_run_dev.sh --stop      # 둘 다 종료
bash scripts/step5_run_dev.sh --foreground  # 포어그라운드 (Ctrl+C 종료)

# 또는 셸 둘
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
streamlit run frontend/app.py             # http://localhost:8501
```

확인:

```bash
curl http://localhost:8000/health | jq
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

### 9. (선택) Telegram inbox watcher

LinkMind-Inbox 같은 텔레그램 채널에 URL/메모 던지면 자동 ingest + 채널 자동 정리:

```bash
# https://my.telegram.org 에서 API ID/Hash 발급 후 env/dev.env 에 채우기
# (자세히는 docs/telegram_setup.md)
bash ai_agents/telegram_inbox_watcher.sh                # 첫 실행: SMS 인증
bash ai_agents/telegram_inbox_watcher.sh --daemon       # 백그라운드 daemon
bash ai_agents/telegram_inbox_watcher.sh --restart      # 코드 변경 후 재기동 (idempotent)
bash ai_agents/telegram_inbox_watcher.sh --backfill 50  # 채널의 최근 N개도 처리
tail -f /tmp/telegram-watcher.log                     # 로그
```

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

### LinkMind ↔ OpenClaw 분리

OpenClaw 는 **frontend agent** (사용자가 직접 대화하는 layer). LinkMind 는 **backend knowledge OS** (지식 저장·검색·답변 layer). 둘은 HTTP API 로만 통신. OpenClaw 가 breaking change 나도 LinkMind 는 영향 없음.

자세한 내용: [docs/openclaw_integration.md](docs/openclaw_integration.md)

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
| **C wave-1 (Telegram inbox)** | Telethon daemon, 채널 메시지 → 자동 ingest + URL 라우팅 + topic 매핑, 처리 후 채널에서 자동 삭제 (inbox 패턴), GitHub README HTML cleanup, PDF title placeholder 거름 | ✅ 완료 (실 환경 검증) |
| **리팩토링** | `scripts/` 는 .sh 만 / `backend/jobs/` batch python / `ai_agents/` client agent — 5 카테고리 135 tests | ✅ 완료 |
| C wave-2 (Slack workspace ingest) | ⚠️ 옛 archive 폴더 사용자가 삭제. Slack API 직접 (slack_sdk) 또는 slackdump 재 export → 워크스페이스 전체 채널 ingest + thread/첨부, URL 자동 라우팅 | 진행 예정 |
| 2 후반 (AI 카테고리/feedback/dataset exporter) | AI 카테고리 강화, feedback 테이블, dataset exporter (JSONL) | |
| 3 | 이미지/OCR/멀티모달 RAG, TEI 임베딩 전환, MinIO object storage | |
| 4 | **sVLL LoRA 파인튜닝** (LLaMA-Factory + Qwen2-VL), vLLM 서빙 | |
| 5 | Continuous training loop, on-prem AI 엔진 완성 | |

자세한 backlog 와 phase 별 완료/미구현 항목 — `docs/features_backlog.md` 참고.

## 라이센스

(미정 — 사용자 결정 대기)

## 기여 / 코드 스타일

- Python typing 사용, Pydantic schema, async/await 우선
- FastAPI router 구조, 함수 단위 분리
- 주석은 한국어 OK, 충분히 작성
- 과도한 OOP / 디자인 패턴 지양
