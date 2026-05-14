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

Postgres / Qdrant / Ollama / OpenWebUI 를 한 번에 띄우고 healthcheck 통과까지 대기:

```bash
bash scripts/step2_setup_infra.sh        # docker compose up -d + healthy 대기
bash scripts/step2_check_infra.sh        # 4개 서비스 연결성 + 포트 검증
```

옵션:

```bash
bash scripts/step2_setup_infra.sh --phase2     # + TEI / MinIO
bash scripts/step2_setup_infra.sh --recreate   # 컨테이너 강제 재생성
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
python scripts/step4_init_qdrant.py      # 컬렉션 생성
bash scripts/step4_check_qdrant.sh       # 컬렉션 존재 + vector dim 일치 확인
```

### 6. FastAPI 백엔드 띄우기

```bash
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

확인:

```bash
curl http://localhost:8000/health | jq
```

### 7. Streamlit UI

```bash
streamlit run frontend/app.py
# 기본: http://localhost:8501
```

### 8. 첫 자료 수집 (URL 한 건)

```bash
python -m backend.ingest.url https://arxiv.org/abs/2401.01234
```

또는 Streamlit `➕ 자료 추가` 탭에서 텍스트 직접 붙여넣기.

### 9. (선택) OpenClaw 설치

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
| 1 | Postgres + Qdrant + 기본 ingest + 임베딩 + Semantic Search | 진행 중 (scaffold 완료) |
| 2 | AI 요약/태깅, Streamlit RAG UI, Slack export 파서, TEI 전환 | 다음 |
| 3 | 이미지/OCR/멀티모달 RAG, feedback 테이블, **dataset export** | |
| 4 | Docker 전체 통합, OpenWebUI 연동, **sVLL LoRA 파인튜닝**, vLLM 서빙 | |
| 5 | Continuous training loop, on-prem AI 엔진 완성 | |

## 라이센스

(미정 — 사용자 결정 대기)

## 기여 / 코드 스타일

- Python typing 사용, Pydantic schema, async/await 우선
- FastAPI router 구조, 함수 단위 분리
- 주석은 한국어 OK, 충분히 작성
- 과도한 OOP / 디자인 패턴 지양
