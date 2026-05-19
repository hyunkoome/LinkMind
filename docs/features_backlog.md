# Feature Backlog

세션 중 사용자가 요청한 기능들을 검증/구현 phase 별로 정리. 우선순위는 Phase A → B → C 순.
TodoWrite 는 "현재 세션의 작업 단계" 추적용, 이 문서는 "기능 단위 backlog" 의 source of truth.

---

## Phase A — ✅ 완료 (2026-05-15)

### A1. LLM 모델 교체 + 한국어 강제 prompt ✅
- exaone3.5:7.8b 검증 후 사용자 결정으로 **qwen2.5:14b** 채택 (영어 본문 한국어 요약 품질 우수)
- ask `rag_system` v1 — 한국어 + 영어 키워드 보존
- summary `summary_system` **v3** — 출력 무조건 한국어 + 영어 본문도 번역 + 예시 키워드 베껴쓰기 금지 + 최소 10 bullet + 끝줄 5-10 해시태그
- `_generate_and_save_summary` user message 에 "한국어로 요약하라" prefix (system 만으로 안 끌리는 모델 방어)

### A2. Settings UI / DB 기반 런타임 설정 ✅
- DB 스키마: `app_settings` (key/value), `prompts` (name/version/content/is_active history)
- `backend/runtime_settings.py` — DB-backed in-memory 캐시
- `backend/api/settings.py` — `GET/PUT /settings/llm`, `GET /settings/llm/models`,
  `GET/POST /settings/prompts/{name}` + version 활성화
- prompt 변경은 새 version 으로 저장 (Versioned analysis 원칙, 학습 데이터 추적)
- ask.py, ingest/url 이 모두 DB 의 active prompt 를 사용 (코드 상수는 시드 default)

### A3. URL ingest 강화 ✅
- 논문/article 페이지의 **abstract** 가 있으면 본문 전체가 아니라 abstract 만 LLM 요약 입력으로
  (citation_abstract meta, arxiv `<blockquote class="abstract">`, og:description 순)
- HTML 의 **페이지 keywords** 추출 (citation_keywords meta, arxiv subject classes, JSON-LD keywords)
- LLM 요약 끝줄의 `#tag1 #tag2 ...` 해시태그 파싱
- 페이지 keywords + LLM hashtags 머지 → dedup → `items.tags` 저장 (최대 10개)
- 최소 5개 목표 (LLM 이 적게 뽑으면 그대로)

### A4. 해시태그 검색 ✅
- `/search` query 토큰 분리 — `#키워드` 자동 감지, 텍스트는 비고 태그만이면 Postgres GIN 으로 최신순, 혼합이면 Qdrant + tag filter
- `SearchRequest.tags` 명시 파라미터도 지원
- `_generate_and_save_summary` 후 Qdrant chunk payload 도 tags 갱신 (`set_payload_for_item_chunks`)

### A5. Streamlit Settings 탭 ✅
- LLM provider dropdown + ollama 설치 모델 dropdown (`/settings/llm/models` 사용)
- prompt textarea (rag_system, summary_system) + 저장 시 새 version 활성화
- 버전 히스토리 보기 / 옛 버전 활성화 endpoint (`/settings/prompts/{name}/activate`)
- Search 탭에 tags 표시 + `#tag` 검색 hint
- Ingest 탭: URL/자동 + source 강제 dropdown + PDF 파일 업로드

### A6. main.py lifespan + 검증 ✅
- 시작 시 `runtime_settings.seed_and_load()` — 없으면 v1 prompt 자동 시드 + DB → 캐시 적재
- 라우터 등록 완료: health / ingest / search / ask / settings / files
- schema 변경은 CREATE TABLE IF NOT EXISTS 라 운영 DB 에도 안전하게 적용
- 검증된 케이스: arxiv 2106.09685 (LoRA), GitHub microsoft/LoRA, YouTube 단일/플레이리스트, PDF 3건

---

## Phase B — ✅ 완료 (2026-05-15)

### B1. YouTube 단일 영상 ingest ✅
- `backend/ingest/youtube/` — yt-dlp 메타 + youtube-transcript-api 자막(ko→en)
- 자막 없는 영상은 description 으로 요약 + `paper_keywords` 에 `no-transcript` 자동 주입
- source_type=`youtube`, source_id=video_id, source_url=canonical watch URL
- 검증: `PYr-LSOf2OY` (자막 없음, description 으로 요약 → 10 tags)

### B2. YouTube 플레이리스트 ingest ✅
- 옵션 1 채택: 플레이리스트 1개 = item 1개 (source_type=`youtube_playlist`), `extract_flat=in_playlist` 로 영상 목록만
- raw_content = 헤더 + 영상 목록 + 끝에 yt-dlp 원본 dict JSON (loss-less)
- LLM 요약 입력은 "Raw (yt-dlp)" 마커 앞까지만 (영상 목록), abstract cap 8000 적용
- 검증: ETH Spring 2025 강의 50+개 → 1010자 한국어 요약 + 10 tags
- 옵션 3 (영상 individually 도 ingest) 는 향후 `--deep` 플래그로 가능 — 보류

### B3. GitHub repo ingest ✅
- `backend/ingest/github/` — GitHub REST API (`/repos`, `/readme`, `/languages`)
- 인증 없으면 60 req/hour, `GITHUB_TOKEN` 으로 5000. README base64 decode
- paper link 자동 탐지 (arxiv URL / DOI / paperswithcode) → `has-paper-link` 라벨
- **라이선스 SPDX hashtag 강제 보장** (paper_keywords 의 맨 앞에 두어 _TAG_MAX 잘림 방지)
- 검증: microsoft/LoRA → 10 tags (다만 옛 ingest 라 license 우선순위 fix 전 — 재실행 필요)

### B4. PDF 파일 ingest ✅
- `backend/ingest/pdf/` — pypdf 우선 → pymupdf fallback, `_sanitize_text` 로 NUL byte 제거
- 원본 PDF 는 `volumes/archive/<yyyy>/<mm>/<hash[:2]>/<hash>` 에 loss-less 보존
- attachments 테이블 등록 (mime=`application/pdf`)
- abstract 자동 탐지 (PDF 앞 5000자에서 "Abstract" 섹션 regex)
- `POST /ingest/pdf` (URL) + `POST /ingest/pdf/upload` (multipart) 둘 다 지원
- `GET /files/{file_hash}` — PDF 브라우저 inline 표시. multipart 업로드 PDF 의 source_url 을 path-only `/files/{hash}` 로 저장
- 검증: 3개 PDF (SLAM Multi-Camera / FAST-LIVO2 / LiDAR Teach-Radar Repeat) — 39 / 131 / 103 chunks, 한국어 요약 + tags

### B 공통 — 새 ingest 모듈 패턴 ✅
- 모두 url ingest 의 helper 재사용: `ExtractedDoc` 데이터클래스, `_generate_and_save_summary`, `_embed_and_index`
- 새 source 추가 시: (1) fetch + ExtractedDoc 채움 → (2) insert_item + source-specific metadata → (3) helper 호출. 패턴 단순
- `POST /ingest/auto` — host 자동 분류 dispatcher

---

## Phase B follow-up — ✅ 완료 (2026-05-16)

- ✅ SLAM Multi-Camera / FAST-LIVO2 / LiDAR Teach-Radar PDF 3건 모두 `backfill_summary.py --force` 로 v3 prompt 한국어 재요약 + 한국어 해시태그
- ✅ `ingest --force` 옵션 신설 (url/github/pdf/youtube + API + CLI + Streamlit 체크박스) — 동일 hash 의 기존 item 도 summary/tags/source_metadata 재계산. raw/chunks 보존. `refresh_existing_item_analysis` 헬퍼 공통화
- ✅ GitHub `raw_body` hash 안정화 (stars/forks 카운터 제거 + sorted topics/languages) → idempotent 보장
- ✅ microsoft/LoRA `--force` re-ingest → `#MIT` 라이선스 태그 정상 진입 + qwen2.5:14b v3 한국어 요약
- ✅ `/files/{hash}` endpoint 동작 검증 (200 inline PDF + 400/404 에러 케이스, Streamlit path 결합)
- ✅ YouTube 영상/playlist 썸네일 → attachments role='thumbnail' (`_pick_best_thumbnail` 가장 큰 해상도 선택, multimodal 학습 데이터)
- ✅ PDF figure 추출 — pymupdf `page.get_images()` + xref dedup + 200×200 미만 skip → attachments role='figure'
- ✅ PDF abstract regex 보강 — em-dash/colon/uppercase 라벨 + 'SUPPLEMENTARY MATERIAL'/'I NTRODUCTION'(글자 사이 공백)/Index Terms 종결 + 라벨 없는 fallback (Introduction 직전 단락)
- ✅ `Qdrant orphan` 정리 헬퍼 (`delete_chunks_for_item`)
- ✅ `attachments` insert 일반화 (`repository.insert_attachment` — PDF 본체/figure/thumbnail 공통)

---

## Phase 2.5 — Topic 그룹핑 (멀티모달) ✅ 완료 (2026-05-16)

같은 "지식 단위" (논문 + 코드 + 영상 + 블로그) 가 자동으로 한 topic 으로 묶이는 구조. sVLL 학습 시 multi-modal 페어 생성의 기반.

### 2.5.1 — backend / 그룹핑 자동 매핑 ✅

- 새 테이블: `topics(id, slug, title, description, primary_external_id JSONB, tags, ...)` + `item_topics(item_id, topic_id, role, confidence, source, note)`
  - slug 규칙: `arxiv:<paper_id>` (버전 제외), `github:<owner>/<repo>`, `doi:<lowercased>`, `yt:<video_id>`, `ytpl:<playlist_id>`
  - role: paper / pdf / code / video / playlist / blog / note
  - source: 'auto' / 'manual' (UPSERT 우선순위로 manual 이 auto 안 덮어쓰지 못함)
- `backend/utils/external_ids.py` — 표준 식별자 추출 + 정규화 (URL + 텍스트 모두). `ExternalId` dataclass + `extract_external_ids(url, text)` + `primary_external_id(ids)` (arxiv > doi > github > yt > ytpl 우선순위)
- ingester 4종 (url/pdf/github/youtube) — ingest 시 source_url + 본문에서 external_ids 추출 → `source_metadata.external_ids` 채움 + `auto_link_topics(item, ids)` 호출. primary 는 confidence 1.0, cross-modal 단서는 0.7
- `backend.jobs.backfill_external_ids` — 기존 item 들에 소급 적용 (`source_metadata.external_ids` 키 유무로 idempotent, `--force` 재계산)

### 2.5.2 — API / UI ✅

- `GET /topics?limit=N` — 최신 updated 순 + item_count
- `GET /topics/{id_or_slug}` — 상세 + 그 안의 모든 item (role 정렬). slug 안의 '/' 도 처리 (`{slug:path}`)
- `GET /topics/items/{item_id}` — item 의 topic membership (검색 결과 보강)
- `POST /topics/items/{id}/link` — 수동 link (source='manual')
- Streamlit **Topics 탭** 신규: 왼쪽 topic 목록 ↔ 오른쪽 상세 (description / items role 별)
- Search 결과 보강: hit 별로 `📚 topics: slug(role) ...` 칩 표시
- 수동 link UI (item_id + slug + role + note)

### 2.5.3 — Topic description 자동 생성 ✅

- `backend.jobs.generate_topic_descriptions` — 자식 item 2개 이상인 topic 에 대해 (role, title, summary) 합쳐 LLM 으로 한국어 5-8 bullet 합성 → `topics.description`
- `_TOPIC_SYSTEM_PROMPT` 별도 — "같은 주제를 여러 modality 가 어떤 관점에서 다루는지" 명시
- 검증: arxiv:2106.09685 (LoRA paper + GitHub) + arxiv:2511.20343 (AMB3R paper + GitHub + project page)

### 2.5.4 — 실 데이터 검증 ✅

- arxiv:2106.09685 — LoRA paper URL + microsoft/LoRA GitHub → 같은 topic 자동 묶임
- arxiv:2511.20343 — AMB3R paper + HengyiWang/amb3r + project page (hengyiwang.github.io) 3 modality 자동 묶임 + Livioni/OmniVGGT 는 별 topic (false positive 없음)
- microsoft/LoRA README 의 paper link 6개 → arxiv:1907.11692/2006.03654/1902.00751/2101.00190 + huggingface/peft 등 6개 secondary topic 자동 생성

---

## Phase 2.5 다음 wave ✅ 완료 / ⏸ 일부 보류 (2026-05-16)

- ✅ 검색 결과에 같은 topic 의 다른 modality 인라인 노출 — Streamlit Search 탭의
  hit 별 expander 안에 sibling item (role + url + 첫 줄 요약). primary topic
  (confidence 최대) 기준 fetch.
- ✅ arxiv API 시드 (`backend.jobs.seed_arxiv_metadata`) — export.arxiv.org 의 atom
  feed 로 `arxiv:<id>` topic 의 title / authors / published / summary / primary_category
  자동 보강. batch (한 호출에 최대 100 id) + `tags` 에 `arxiv-seeded` idempotent 마커.
  검증: 11개 arxiv topic 모두 paper 제목 정확히 갱신 (RoBERTa / DeBERTa / Adapter /
  Prefix-Tuning 등 microsoft/LoRA README 가 가르킨 paper 들).
- ✅ Streamlit manual link UI 의 topic slug autocomplete — selectbox (기존 topic
  목록) + 새 slug 직접 입력 fallback.
- ⏸ paperswithcode slug → github_repo 자동 연결 — **외부 API 종료로 보류**.
  paperswithcode.com 이 Hugging Face 에 인수되어 `/api/v1/papers/` 가 302 redirect
  (huggingface.co/papers). HF papers API (`huggingface.co/api/papers/{id}`) 는
  github_repo 매핑 없음 (`ai_summary` / `authors` 정도만). 대안 없으면 GitHub README
  의 arxiv 링크 자동 cross-link (이미 구현됨) 으로 충분.

---

## Testing 인프라 ✅ 완료 (2026-05-16)

새 함수 추가 시 같은 PR 안에서 단위 테스트도 동반 작성하는 정책 (CLAUDE.md §9 참고).

- pytest marker 5종: 마커 없는 default (`cpu`) / `embedding` / `integration` / `llm` / `gpu`
- `tests/` 디렉토리: `tests/{integration,embedding,gpu,llm}/` 와 root (default suite)
- 실 fixture: `tests/resources/2003.02014v1.pdf` (SLAM Multi-Cam ICRA 2020, 2.4MB) + `2408.14035v2.pdf` (FAST-LIVO2, 39MB) + `test_urls.json` (그룹핑 fixture)
- `scripts/tests/` — ci/local/total 디렉토리로 환경 별 분리, README 포함
- GitHub Actions CI (`.github/workflows/ci.yml`) — `requirements-test.txt` 의 lightweight 의존성 + `pytest -m "not gpu"` (GPU 만 deselect)
- 총 95 tests:
  - default 83건 (pure unit + mock — pdf abstract, url classify/tags, hashtag, yt/github parse, github API mock, topic auto-link 5종, real URL 그룹핑 4종, PDF pipeline 8종, external_ids 22종 등)
  - embedding 3건 (`sentence-transformers MiniLM-L6-v2` CPU smoke)
  - integration 4건 (FastAPI Topics API live)
  - llm 2건 (Ollama 짧은 chat smoke, qwen2.5:7b/14b)
  - gpu 3건 (torch.cuda + sentence-transformers cuda + LocalEmbeddingProvider)

검증 시간 (로컬 RTX 4090):  cpu 4s / embedding 19s / integration 0s / llm 3s / gpu 14s = **5 카테고리 PASS**.

---

## ENV cleanup ✅ 완료 (2026-05-16)

env 는 **인프라 위치** + **시크릿** 만. LLM 런타임 선호 (provider/model) 는 DB `app_settings` + UI Settings 탭.

- 제거: `DEFAULT_LLM_MODEL` (dead env, /health 표시만), `DEFAULT_LLM_PROVIDER` (DB+UI), `OLLAMA_MODEL` / `OPENAI_MODEL` / `ANTHROPIC_MODEL` (DB+UI)
- 유지: `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `OPENROUTER_API_KEY` (시크릿), `OLLAMA_BASE_URL[_LOCAL]` (인프라 위치), `POSTGRES_*` / `QDRANT_URL` / `EMBEDDING_*` / `HF_HUB_OFFLINE`
- `backend/runtime_settings.snapshot()` 의 `env_defaults` → `config_defaults` 라벨 변경 (의미 명확화 — 실제는 backend/config.py 의 Field default)
- Postgres user 비번 `8gD7XF51...` → `real2real` 단순화 (dev 환경 한정)

---

## Phase C wave-1 — Telegram inbox ✅ 완료 (2026-05-16)

LinkMind-Inbox 텔레그램 채널 → 자동 ingest 풀 파이프라인. 사용자가 채널에 URL/메모
던지면 watcher 가 받아 LinkMind 로 흘려보내고, 성공 시 채널에서 메시지 자동 삭제
(inbox 패턴 — 처리 안 된 것만 시각적으로 남음).

- `backend/ingest/telegram/__init__.py` — TelegramMessage dataclass +
  `ingest_telegram_message` (URL 있으면 host 별 ingester 자동 라우팅 + topic 그룹핑,
  URL 없으면 `source_type='telegram'` note 저장 + external_ids/auto_link_topics 적용).
  Export 폴더 파서 (`parse_export_messages`, `ingest_telegram_export`).
- `backend/ingest/telegram/__main__.py` — `python -m backend.ingest.telegram <path>`
  로 Telegram Desktop 의 result.json 폴더 일괄 import.
- `ai_agents/telegram_inbox_watcher.py` — Telethon (사용자 계정) 기반 watcher daemon.
  첫 실행 시 SMS 인증, `volumes/telegram/inbox.session` 자동 생성. NewMessage event
  listener + `--backfill N` 옵션으로 채널 history 도 한 번에. `_ingest_successful`
  로 ingest 성공 판단 후 `msg.delete()` 호출 → 채널 자동 정리.
- `ai_agents/telegram_inbox_watcher.sh` — bash wrapper. `--daemon` / `--restart`
  idempotent (기존 process 자동 정리), `--stop` / `--status` / foreground.
- `backend/config.py` — TELEGRAM_API_ID/HASH/SESSION_PATH/INBOX_INVITE/
  DELETE_AFTER_INGEST 필드 (시크릿/위치만 env, 런타임 선호 X).
- `requirements.txt` — `telethon>=1.36.0` 추가.
- `docs/telegram_setup.md` — API 발급 / 첫 인증 / 평소 사용 / inbox 패턴 / 트러블슈팅.
- Tests (17건, default suite): `tests/test_telegram_parser.py` + fixture
  `tests/resources/telegram_export_sample.json` (5 메시지 모사).

동반 fix (Telegram 안정성 + 다른 ingester 공통 문제):
- GitHub README **raw HTML strip** (`_clean_readme_html`) — `<h2>`, `<a href>`,
  `<img>`, `<code>` 처리. paper_links 자동 검출 유지. chunks/snippet 노이즈 제거 +
  LLM 요약 입력 정제. OmniVGGT-official 같은 HTML-heavy README 검증.
- **PDF Title placeholder 거름** (`_extract_pdf_title`) — 'Microsoft Word - foo.docx',
  'Untitled', `*.tex` 등 패턴 거름 + body 첫 줄에서 paper title fallback. FAST-LIVO2
  같은 metadata 없는 PDF 도 title 정상.
- watcher daemon `--daemon`/`--restart` 가 기존 process pkill + 새로 띄움 idempotent
  (race / 옛 코드 잔존 방지).

실 데이터 검증 (사용자 환경, RTX 4090 + qwen2.5:7b):
- Live 흐름: arxiv URL / GitHub URL / 텍스트 메모 던짐 → 즉시 ingest + 채널 삭제.
- backfill 흐름: 채널의 옛 메시지들 일괄 처리 + 모두 삭제 (채널이 비워짐).
- HTML cleanup 효과: OmniVGGT-official → 한국어 596자 요약 + 10태그 (이전엔 NULL).

---

## 리팩토링 ✅ 완료 (2026-05-16) — scripts / backend.jobs / ai_agents 분리

CLAUDE.md §3 NEVER ('backend 안에 봇 코드 X') 정신 유지하면서 폴더 의도 명확화.

- `scripts/` = OS / 인프라 셋업 셸 스크립트 (.sh) 만. stepN_*, install_*, slack_export.
- `backend/jobs/` = backend 모듈 호출하는 batch python (이전엔 scripts/.py).
  `backfill_summary`, `backfill_external_ids`, `seed_arxiv_metadata`,
  `generate_topic_descriptions`, `init_db`, `init_qdrant` (이전 step4_init_qdrant
  이름 단순화). 호출: `python -m backend.jobs.<name>`. `sys.path.insert` hack 제거.
- `ai_agents/` = LinkMind 의 client agent (backend 외부 — NEVER 정신).
  `telegram_inbox_watcher` (.py + .sh). README 에 새 agent 추가 규칙.

검증: 5 카테고리 (cpu/embedding/integration/llm/gpu) 전부 PASS, **135 tests**
(이전 130 + `_ingest_successful` 5건). `bash scripts/step5_run_dev.sh --status`
로 backend/frontend/telegram 셋 다 가동 OK.

별도 wave 로 git author email 통일 — 34 commit history rewriting (filter-repo) +
force push: `hyunkoo.dev@watanow.com` → `hyunkoome <hyunkookim.me@gmail.com>`.

---

## RAG `/ask` 답변 품질 ✅ 완료 (2026-05-16)

이전엔 `/ask` 의 답이 "SLAM 은 로봇이 위치 파악…" 같은 일반 LLM 정의 + `[1]` 인용
정도 — 사용자가 가진 자료의 깊이 안 보임. archive 와 다를 바 없는 상태였음.

수정:
- `backend/api/ask.py` 의 context block 보강:
  - 이전: `[i] title\nURL\nsnippet(300자)` 만
  - 이후: `[i] title\nURL\nsource_type\nTags: #...\n요약: {item.summary 1500자 cap}\n관련 chunk: {snippet 400자 cap}`
- `rag_system` prompt v3 (DB 의 prompts 테이블 + `backend/runtime_settings.RAG_SYSTEM_PROMPT_SEED` 둘 다):
  - "답변 본문 + 이 자료들이 다루는 측면" 두 단락 강제
  - "[Context] 의 구체적 사실/방법/한계 인용 우선, 일반 정의보다 자료 깊이 우선"
  - "Context 의 자료를 반드시 인용. 인용 없는 답변은 안 됩니다."

검증: "SLAM 이 뭐야?" 에 답이 "FAST-LIVO2 의 LiDAR-IMU-이미지 융합 / Multi-Cam SLAM
의 adaptive initialization" 같은 자료 구체 인용 + "이 자료들이 다루는 측면" 단락
포함. 인용 3개. 응답 시간 ~3분 (qwen2.5:14b — 별 wave 에서 가벼운 ask 모델 분리
또는 streaming 도입 예정).

남은 ask UX (Phase 2 후반 또는 4):
- ask 전용 더 작은 모델 (qwen2.5:7b 또는 ask-tuned) — Settings 에 ingest_model /
  ask_model 분리 필드
- streaming response (Streamlit 첫 토큰부터 표시)
- 궁극적으로 **sVLL LoRA 파인튜닝** (Phase 4) — 사용자 데이터 학습 모델로 ask 까지
  처리. 학습 데이터 self-loop 완성.

---

## Phase C wave-2 — Slack 워크스페이스 일회성 backfill ✅ 모듈 완료 / 🚧 ingest 진행 중 (2026-05-19 늦은 저녁)

⚠️ **배경 (2026-05-16 외출 전 사용자 알림)**:
- 옛 `archive/slack_export/public_2026-05-14/` 폴더는 사용자가 삭제
- 사용자가 **Slack 구독을 곧 해제 — 내일부터는 Slack 안 씀**
- 즉 wave-2 는 **일회성 backfill** 만 의미. incremental sync / realtime watcher 불필요.

**완료 항목 (2026-05-19)**:
1. ✅ `bash scripts/slack_export.sh` 로 새 export — workspace 전체, files=true.
   결과: 183 채널 / 14241 메시지 / 625 MB / 16 첨부, `archive/slack_export/
   full_2026-05-19_20-04-58/` + `latest` symlink. token/cookie cache 살아있어
   재인증 불필요.
2. ✅ **`backend/ingest/slack/` 신규 모듈** (Telegram 패턴 미러):
   - `export_parser.py` — slackdump standard 파싱 (channel 디렉토리 + 날짜별 JSON
     + `attachments/`). `SlackMessage` / `SlackAttachment` dataclass. mrkdwn entity
     정리 (`<url|label>` / `<@U>` / `<#C|name>` / `<!here>`), blocks/raw URL 추출
     + dedup, 시스템 메시지 (`_SKIP_SUBTYPES`: channel_join/leave/topic/bot 등) skip,
     thread 부모 → 자식 `parent_text` 전파, `channels.json`/`users.json` 메타 로드,
     `_slack_permalink` 생성
   - `__init__.py` — `ingest_slack_message` (단일) + `ingest_slack_export` (폴더).
     URL → `_classify_url` 분기 라우팅 (ingest_url/youtube/github/pdf), 첨부 →
     `ingest_document`, caption (thread parent / 첨부 본문 / URL 제거 잔여) →
     user_notes, URL/첨부 없으면 source_type='slack' note 저장
   - `__main__.py` — CLI (`python -m backend.ingest.slack <export> [옵션]`).
     `--channel`, `--workspace-url`, `--force`, `--no-progress`, `--issues-path`,
     `--no-issues`. tqdm 진행률 (postfix: ch / urls / errs / iss). 이슈 manifest
     자동 보존 (기본: `<export_dir 부모>/issues/<ts>/manifest.json` — 사용자 정책
     2026-05-19: archive/slack_export/ 하위에만, /tmp 휘발성 금지).
3. ✅ **CLI: `python -m backend.ingest.slack <export_dir> [옵션]`** + scripts/
   slack_ingest_all.sh (사전 점검 — uvicorn 가동 여부 / GPU 여유 / vLLM 응답 —
   + 한 줄 실행).
4. ✅ **단위 테스트 + fixture**: `tests/test_slack_parser.py` 46 케이스 (mrkdwn 9
   / URL 6 / 첨부 3 / helper 5 / 메타 4 / parse 11 / _resolve_caption 5 + fixture 3).
   `tests/resources/slack_export_sample/` (channels.json + users.json + test-channel/
   2026-05-19.json + attachments/) 신규.
5. ✅ **검증**:
   - 단일 채널 `robot-action-foundation` (1 URL): chunks=10 + 한국어 summary
     1003자 + tags 6개 + title 자동 추출. end-to-end 작동 확인.
   - thread 채널 `가-공부-논문쓰기-image-composition-이미지-물체-추가` (248 메시지):
     218 URLs + 55 notes ingest. github=63 / pdf=12 / url=107 / slack note=53 /
     youtube=5. avg summary 600-800자. thread parent_text → 자식 caption 잘 전파.

**부수 fix (Slack ingest 도중 발견)**:
- ✅ `_classify_url` 의 `/pdf/` path 인식 (`backend/api/ingest.py`) — 기존엔 `.pdf`
  확장자만 검사라 `arxiv.org/pdf/2106.14490` 등이 url 분기로 잘못 라우팅돼서
  readability fallback 만 도는 placeholder 만 생기던 버그. fix: `parsed.path` 의
  `/pdf/` 세그먼트도 pdf 분기 (arxiv / openaccess.thecvf / openreview 등). 회귀
  테스트 `test_classify_pdf_path_segment` 추가.
- ✅ thread 채널 검증에서 발견된 9개 placeholder (8 arxiv pdf URL + 1 unite.ai)
  DELETE — 전체 ingest 시 fix 효과로 PDF 흐름으로 재라우팅됨.
- ✅ **GPU OOM 해결** — vLLM (qwen2.5-7B, 18 GB) + backend uvicorn 의 bge-m3
  (3.78 GB) 가 GPU 거의 점유 → CLI ingest 의 bge-m3 가 추가로 못 들어감.
  단기 fix: `bash scripts/step5_run_dev.sh --stop` 으로 ingest 중에만 uvicorn
  종료. **장기 fix (wave-5+ 후보)**: bge-m3 를 **TEI 컨테이너** 분리 → 모든 프로세스가
  같은 HTTP 서버 호출 → 모델 1번만 GPU. CLAUDE.md §12 의 Phase 2 backlog 항목.

**🚧 진행 중**: `bash scripts/slack_ingest_all.sh` (background, tmux). 평균 ~12 s/msg,
**예상 1.5-2일** (vLLM 요약 + 임베딩 + URL fetch). 완료 후 결과 확인 + manifest
분석 (LinkedIn / project page / mp4 등 패턴별 후속 처리) + step5 재기동.

**slack_sdk 직접 호출은 over-engineering** — 구독 해제 후 코드 거의 dead 자산.
모듈 자체는 향후 다른 Slack 워크스페이스 처리 또는 다시 쓸 때 재사용 가능
(Telegram `ingest_telegram_export` 와 같은 구조).


### C1. Slack export ingest
- `bash scripts/slack_export.sh` 로 slackdump 산출물 재수집
- `backend/ingest/slack/export_parser.py` 작성 — `archive/slack_export/latest/<channel>/<yyyy-mm-dd>.json` + `attachments/` 파싱
- items (source_type=`slack`, source_id=`<team>_<channel>_<ts>`, source_url=permalink) + chunks + Qdrant
- thread 처리 (parent_message_ts → reply 묶음), 첨부 파일 다운로드
- url ingest 의 `ExtractedDoc` + helper 재사용 (YouTube/GitHub/PDF 에서 검증된 패턴)

### C2. 그 외 데이터 소스
- Telegram ingest
- GitHub issue/PR ingest (현재 repo README 만)
- arxiv 모듈 (현재는 URL ingest 가 arxiv abs 페이지를 우회 처리 — 별도 모듈로 정돈 시 citation 메타 더 정확)
- OCR / 멀티모달 이미지 분석 (Phase 3)

### C3. 학습 데이터 파이프라인 (CLAUDE.md Phase 3-5)
- AI 카테고리/태깅 강화 (현재 LLM 해시태그만)
- feedback 테이블 — 사용자 평가 (요약/답변 quality) → Continuous training loop
- dataset exporter — Phase 4 sVLL LoRA 파인튜닝용 JSONL
- TEI 임베딩 전환 (sentence-transformers 로컬 → TEI 컨테이너)
- MinIO object storage 전환 (Phase 2 후반)
- sVLL LoRA 파인튜닝 (Phase 4 — LLaMA-Factory + Qwen2-VL 등), vLLM 서빙
- Continuous training loop (Phase 5)

---

## Phase D — ✅ wave-4 완료 (2026-05-18 ~ 19) — categories 레이어 + Union 그래프 + UX 완성

### D1. fallback topic + cross-modal 데이터 정리 ✅
- `auto_link_topics` 의 fallback — external_id 없는 url 도 `url:item:<uuid>` slug 의
  자체 topic 자동 생성. 그래프에 모든 자료가 일급 시민.
- 기존 193 orphan items 일괄 backfill topic 생성.
- cross-modal title 차용 버그 fix (line 602: `title or x.slug` → `x.slug`) — github
  README 의 arxiv 링크 30개가 다 repo title 차용하던 데이터 중복 해결.
- 181 topics title cleanup (같은 title 다중 topic 그룹의 첫 번째만 유지).

### D2. categories 신규 스키마 + 자동 시드 + 매핑 ✅
- 스키마 (`backend/db/schema.sql`):
  - `categories` (id/slug/label/description/synonyms/color/pinned)
  - `topic_categories` M:N (source/confidence)
- repository helper 7개 (`backend/db/repository.py`).
- `backend/api/categories.py` — GET list/detail, POST upsert/manual link.
- `backend/jobs/auto_link_categories.py` — items.tags 빈도 ≥ 3 분석 → 61 카테고리 자동
  시드 + 796 link 생성. dry-run 지원.
- `find_category_by_slug` 가 topic_count/item_count 동봉 — graph expand 응답의 카테고리
  노드 0/0 표시 버그 해결.

### D3. 3-tier graph endpoint ✅
- `/graph/categories` — 카테고리 + topic (시작 view, item 제외 — 가벼움)
- `/graph/category/{slug}` — 카테고리 expand (카테고리 1 + topic + item)
- `/graph/topic/{uuid}` — 토픽 expand (토픽 1 + 모든 item)
- graph limit 100 → 5000 (max 20000).

### D4. caption append 정책 ✅
- url/pdf/github/youtube/document/telegram 모든 ingest 에 `caption` 파라미터.
- `append_item_user_notes` 헬퍼 — idempotent (같은 caption 두 번이면 dedup) +
  timestamp 구분자 (`--- YYYY-MM-DD HH:MM ---`).
- 같은 URL 새 caption 재공유 시 user_notes 에 누적 (덮어쓰기 X).
- `_strip_urls_for_caption` — URL 만 있고 메모 같이 온 경우 메모 추출.
- 단위 + integration 테스트 (`tests/integration/test_user_notes_append.py` 4 case).

### D5. ingest fail 케이스 6건 자동 처리 ✅
- url-only fallback 의 result key `error` → `fetch_error` (watcher 의
  is_ingest_successful 가 success 로 인식 — medium 403, stibee 200 본문 추출 실패 등).
- url-only 시 자동 user_notes 메모 (archive.org 시도 안내).
- YouTube `/live/{id}` URL pattern 추가.
- GitHub owner-only URL (예: `github.com/graphdeco-inria`) fallback → url ingest.

### D6. vLLM 전환 ✅
- 신규 `backend/llm/vllm_provider.py` (OpenAI 호환 client).
- docker-compose `profile: vllm`, healthcheck start_period 1800s (모델 다운로드 + cudagraph capture).
- env `VLLM_MODEL` (Qwen/Qwen2.5-7B-Instruct), `VLLM_GPU_MEM_UTIL=0.75` 검증.
- `default_llm_provider`: ollama → vllm. 249 NULL summary 자동 backfill (~30분).
- 결과: qwen2.5:14b 3분 → vllm/Qwen2.5-7B 7초 (**~30x 빠름**).

### D7. Frontend 대개편 (Next.js 16 + React 19 + Tailwind v4) ✅
- **i18n 시스템** — `LocaleProvider` + `useT()` hook, ko/en dict, localStorage 보존.
- **3-tier sidebar 트리** (`TopicsTree`) — 카테고리 (▸/▾) → 토픽 (▸/▾) → 아이템.
- **색상 그룹화** (`lib/colors.ts`) — 📄 Articles=녹 · 🎥 Video=빨 · 💻 Code=보 ·
  🌐 Web=파 · 💬 Note=시안. 그룹별 modality 미세 명도 차.
- **selected/related 시각** — 원래 색 정체성 유지 + 사이즈 (1.7×/1.3×/1.0×) +
  non-related 65% darken. 흰색 강제 X.
- **양방향 highlight (`relatedIds`)** — sidebar ↔ graph 동기화 (page.tsx useMemo).
  같은 묶음 (topic + items, category + topics) 자동 강조 + 카테고리 자동 expand +
  scrollIntoView.
- **Union 그래프 (`mergeGraph`)** — 유니온 스테이션 hub-spoke. 클릭 시 그 노드의
  친구들을 기존 그래프에 union 추가 (교체 X). cross-category 시각화 가능.
- **컨텍스트 유지 분기** — `topicHasItemsInGraph` / `categoryHasTopicsInGraph` 헬퍼.
  graph 에 이미 친구 있으면 highlight 만 (fetch X), 없으면 union fetch.
- **handleNodeClick / handleSidebarSelect 통일** — graph 클릭과 sidebar 클릭이 같은
  동작 (이전엔 비대칭).
- **NodeDetails 컴포넌트** — item/topic/category 통합 detail 패널 + 외부 링크 새창.
- **ItemDetails 자동 expand** — itemId 변경 시 collapsed=false.
- **navigation history** — "← 이전" / "← 전체" 두 버튼 (`HistoryFrame` stack).
- **ThemeToggle** — ☀️ light / 🌙 dark / 🖥 system + localStorage + OS prefers
  실시간 반영 + flash-of-wrong-theme 방지 (layout.tsx head inline script).
  Tailwind v4 `@custom-variant dark` 로 `.dark` 클래스 기반.
- **Next.js devIndicators 끔** — 좌하단 N 버튼 제거.
- **Legend 그룹별 섹션** + 선택 상태 안내 + 좌상단 통계 라벨 아래 inline 배치.
- **fullId 일관성 fix** — TopicsTree mismatch 4건 (사이드바 highlight + sub-list 동작).
- **graph 카메라 zoom 안정성** — force layout 미완료 시 250ms × 6회 재시도.

---

## Phase D 다음 (wave-5 후보)

### D8. cross-modality matching (자동 자료 묶기) ⏳
같은 자료의 paper + code + video 가 별 topic 으로 흩어진 케이스 자동 묶기.
단서: title 유사도, README paper link, arxiv abstract 의 github link, 사용자 caption
매칭. 옵션 (a) LLM cluster job, (b) 사용자 manual merge UI, (c) external_id 추출 강화.

### D9. arxiv title 재시드 ⏳
wave-4 의 cross-modal title fix 후 arxiv:* topic 들의 title 이 slug 그대로.
`seed_arxiv_metadata` 재실행으로 진짜 paper title 보강.

### D10. llm_wiki 아키텍처 도입 ⏳
karpathy 의 llm_wiki + vlm_wiki + multi-agent + 자가학습. 일반 RAG 대신 wiki 페이지
단위 (topic = wiki 페이지). [[project-llm-wiki-arch]] memory 참조.

### D11. 카테고리 UI 편집 ⏳
synonyms 추가, 색 지정, pinned 토글, manual link/unlink.
