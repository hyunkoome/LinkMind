# Feature Backlog

> **Phase 번호 기준**: README / `CLAUDE.md §12` (2026-06-02 재번호 — 옛 2+2.5 병합=2, 완료 wiki=3, LoRA=5, training loop=6, OSS/SaaS=7). 이 문서 본문의 일부 옛 번호(LoRA=Phase 4, Phase 2.5, Phase 3 OCR 등)는 **레거시**일 수 있으니 정답은 README/§12.


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
- 궁극적으로 **sVLL LoRA 파인튜닝** (Phase 5) — 사용자 데이터 학습 모델로 ask 까지
  처리. 학습 데이터 self-loop 완성.

---

## Phase C wave-2 — Slack 워크스페이스 일회성 backfill ✅ 완료 (2026-05-19 ~ 23)

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
- ✅ **GPU OOM 해결 (단기 fix)** — vLLM (qwen2.5-7B, 18 GB) + backend uvicorn 의 bge-m3
  (3.78 GB) 가 GPU 거의 점유 → CLI ingest 의 bge-m3 가 추가로 못 들어감.
  단기 fix: `bash scripts/step5_run_dev.sh --stop` 으로 ingest 중에만 uvicorn 종료.
  **장기 fix (D13 진행 중)**: TEI 아닌 **vLLM 으로 임베딩까지 통일** (사용자 결정
  2026-05-23). `vllm-embed` 컨테이너 (`--runner pooling`, bge-m3) → 모든 프로세스가
  HTTP 공유. D13 항목 참조.

**✅ ingest 완료** (2026-05-23): `bash scripts/slack_ingest_all.sh` 4일 걸쳐 완주.
결과 — `archive/slack_export/issues/20260519-220427/manifest.json` 생성. **953
issues / 14241 메시지 = 6.7% 실패율**. placeholder 633 + exception 320.

**manifest 분석** (2026-05-23):
- fix 불가 ~40% — YouTube 영상 삭제/private 174 / LinkedIn login wall 141 / Facebook 65
  / DNS 실패 20 / GitHub repo 비공개 7. 자료 자체가 없거나 익명 익세스 차단.
- 간단 fix — URL protocol 누락 9 / YouTube channel URL skip 14 / openaccess.thecvf 18
  (manifest URL truncated 라 진단 필요). 약 40+건.
- Wayback fallback 후보 — medium.com 88 + SSL/500/ConnectError 24. 약 110+건.
- JS rendering 필요 (어려움) — 네이버 블로그 26 / marble.worldlabs 11 / colab 6 등.

→ **issues 패턴별 재처리는 D12 (placeholder 정리 UI) 안에서 흡수** (사용자 결정
2026-05-23). 단발성 `backend/ingest/issue_reprocess.py` 모듈 만들기 대신, UI 와
인프라 (fetch_error 마커 / 수동 액션 / Wayback / 단축 URL retry / LinkedIn skip
표시) 를 통합한 방향.

**watcher 재기동 시 OOM 발견 (2026-05-23)** — 81 backfill 메시지 중 거의 다 ok=False.
DB 트랜잭션 확인 결과 — `_embed_and_index` 전 commit (line 410) 이라 raw 만 들어간
반쪽 item 49건 (url 29 + youtube 10 + github 8 + telegram 2) 누적. document 47건
(텔레그램 사진 raw="[binary file: ...]") 은 정상 (Phase 3 OCR 영역). → **D13
vLLM-embed 인프라 작업으로 전환**.

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

### C3. 학습 데이터 파이프라인 (CLAUDE.md Phase 3-6) — 2026-05-25 사용자 명확화 반영

**중요 원칙**:
- 지금 단계 (wave-1~5 + D12 + D10 예정) 의 vLLM 모델 (Qwen2.5-7B 등) 은
  **inference 만** — 사용자 데이터로 학습 절대 X.
- 학습은 **Phase 5** 에서 시작 — base 모델 + 사용자 본인 LoRA adapter = "내 자체 모델".
- §11 Privacy 원칙: personal LoRA 는 "사용자 본인 데이터로 본인 모델만" — 운영자 공통
  모델 학습 절대 금지.

**진행 항목**:
- AI 카테고리/태깅 강화 ✅ wave-4 (`auto_link_categories`). D10 wiki classifier
  가 의미 단위 클러스터링으로 진화 예정.
- feedback 테이블 ⏳ — 사용자 평가 (요약/답변 quality) → Continuous training loop.
  wave 6~7 예정. `/ask` 답변에 👍/👎/수정 메모.
- dataset exporter ⏳ — Phase 5. **LLaMA-Factory JSONL 포맷** — raw +
  summary + user_notes + feedback → 학습 input.
- **YouTube 자막 재시도 보강 (raw→summary→wiki 체인)** ⏳ — Phase 5 직전 (2026-06-02
  사용자 결정, MVP 급하지 않아 미룸). 자막이 IP rate-limit / uploader disabled 로
  빠진 영상(특히 Shorts)은 raw_content 가 빈약 → summary/wiki 도 빈약. throttle 은
  시간 지나면 풀리므로, 실패한 YouTube item 을 나중에 모아 ① 자막 재시도 →
  raw_content 보강 ② summary 재생성(보강된 raw 기반) ③ 연결된 wiki_pages 를 stale
  (`body_status='pending'`) 마킹 → writer daemon 자동 재합성. `backend/jobs/` 에
  dry-run + idempotent 잡으로. **위키 본문은 item.summary 기반이라(retriever.py:53,
  writer.py:548) raw 만 보강해선 위키가 안 바뀜 — 반드시 summary→wiki 체인까지 태워야
  함.** Phase 5 학습 데이터 품질 향상이 목적.
- TEI 임베딩 전환 🚫 폐기 — D13 (2026-05-23) 에서 **vLLM-embed 로 대체** (self-host
  정체성 일관성).
- MinIO object storage ⏳ — Phase 2 후반. 현재 로컬 FS + `volumes/archive/` 4.7GB 로 충분.
- **sVLL LoRA 파인튜닝** ⏳ — Phase 5:
  - 플랫폼: **PyTorch 기반**, **LLaMA-Factory** (UI + CLI, Qwen/LLaMA/Mistral/Qwen2-VL
    + LoRA + QLoRA + DPO/RLHF + vision-language 지원). 대안: Unsloth (메모리 효율 ↑),
    Axolotl (yaml config), torchtune (PyTorch 공식).
  - base 모델: **Qwen2-VL-7B-Instruct** (vision-language → sVLL = small **V**ision-
    **L**anguage **L**LM). LinkMind 의 이미지 자료 (PDF figures, Telegram 사진
    1,751건, YouTube thumbnails) 까지 학습 input.
  - 결과: **사용자 본인 LoRA adapter** (~수 MB) — base 는 공유, adapter 만 개인별.
  - 환경: 별 conda env `linkmind-train` (CLAUDE.md §4 — 학습용 환경 별도 생성).
  - GPU 분배: vLLM 중단 → LLaMA-Factory ~12GB → 학습 종료 후 vLLM 재가동.
  - 서빙: vLLM 으로 `--enable-lora --lora-modules linkmind=path/to/adapter` —
    adapter swap 가능.
- Continuous training loop ⏳ — Phase 6. 자가학습 — 주기적 (예: 매주) feedback
  누적 → LoRA 재학습 → 새 adapter 배포.

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

### D10. llm_wiki 아키텍처 ✅ wave-1+2 완료 (2026-05-26)

karpathy llm_wiki + multi-agent (physics-intern state-centric) + YAML prompt (ml-intern)
패턴. 일반 chunk-RAG 대신 wiki 페이지 단위 (topic = wiki 페이지). 자세한 설계 +
구현 trace 는 [docs/llm_wiki_design.md](llm_wiki_design.md).

**완료 항목 (10 step)**:

| step | 항목 | 결과 |
|---|---|---|
| wave-1a | schema 4 테이블 (wiki_pages + wiki_page_versions + wiki_page_items M:N + agent_runs) | ✅ 65 statements migrate |
| wave-1b | AgentBase ABC (state-centric, template method) + YAML prompt loader + agent_runs 자동 적립 | ✅ `backend/agents/base.py` |
| wave-1c | retriever + writer + `prompts/writer_v1.yaml` | ✅ smoke OK |
| wave-1d | classifier (embedding 후보 + LLM JSON M:N + parse retry) + 새 wiki_page 'stale' 마킹 | ✅ |
| wave-1e | wiki API 8 endpoints (list/detail/regenerate/search/classify/keywords-add-remove/autocomplete) | ✅ |
| frontend rename | frontend_v2 → frontend (44 refs sed) + 옛 Streamlit 폐기 | ✅ |
| wave-1f | Qdrant `linkmind_wiki_pages` 컬렉션 + body embedding upsert (writer hook) + Qdrant search 우선 (FTS fallback) | ✅ |
| wave-1g | `wiki_backfill_from_topics.py` (23,852 pages) + frontend `/wiki/page.tsx` + `[slug]/page.tsx` + `WikiBody` + `KeywordsEditor` | ✅ |
| wave-2c | analysis_worker `_classify_to_wiki` hook (텔레그램 ingest 자동 wiki 분류) | ✅ |
| wave-2d | keywords (schema TEXT[] + writer prompt 자동 추출 + 3 API + view/edit UI + matching wiki filter) | ✅ |

**추가 작업**:
- ✅ arxiv URL hook (`backend/ingest/arxiv/`) — 신규 arxiv/IEEE/DOI URL 자동 진짜 제목
- ✅ wiki body 구조 정리 — 5 섹션 narrative 만 (Sources/Cross-links/Keywords 는 aside)
- ✅ Relationship (Cross-links 이름) + 빈 섹션 표시
- ✅ daemon 분리 — `wiki_writer_worker` (lifespan, stale 만) + `wiki_writer_batch` CLI (사용자 직접)
- ✅ `scripts/run_wiki_backfill.sh` — start (자동 SIGKILL 재시작) / stop / restart / tail / status / concurrency
- ✅ **성능 4.6x** — vLLM `--enable-prefix-caching` + `--max-num-batched-tokens 16384` + max_tokens 1024 + concurrent N=4. page 17초 → 3.7초. ETA 4.7일 → ~1일

**자동 흐름 완성** — 사용자 명시 "텔레그램 ingest → 자동 wiki body":
```
텔레그램 → ai_agents → /ingest/url (arxiv API hook)
   → items raw + 채널 삭제 (즉시)
   → analysis_worker: chunks + summary + classifier (자동 wiki 매핑 + stale 마킹)
   → wiki_writer_worker daemon: 자동 body 합성 (~15s/page)
   → 사용자 wiki page 열 때 항상 ready
```

**wave-3 다음 작업**:
- 🚧 critic agent 본격 (citation 검증 + contradiction flag + writer 후처리)
- 🚧 lint job (모순/stale/orphan)
- 🚧 `/ask` 답변 filing-back (wiki 적립)
- 🚧 dataset exporter (Phase 5 LoRA 학습 입력)

### D10.5. ItemDetails user_notes append textarea 통합 ⏳ (D10 안정화 후 작은 wave)

현재 `frontend/components/ItemDetails.tsx` 의 user_notes 는 PATCH set 방식 (덮어쓰기).
**모든 viewer 의 1급 기능** 으로 cleanup 의 ActionPanel 의 textarea (POST `/items/{id}/notes`
append, idempotent) 를 ItemDetails 에도 통합. 사용자 결정 (2026-05-25):
> "user_notes 추가는 모든 데이터 대상이라 cleanup 페이지 국한 X 인 다른 기능"

- 작업 단위: 작음 (반나절 이내) — backend POST endpoint 이미 있음.
- 영향: graph 의 모든 item 클릭 시 user_notes append 가능 — wiki 페이지 viewer 에서도 자연 활용.

### D11. 카테고리 UI 편집 ⏳
synonyms 추가, 색 지정, pinned 토글, manual link/unlink. D10 wiki 자동 분류 안정화 후
필요 줄어들 수 있음 (agent 가 자동 처리하면 사용자 편집 minimal).

### D12-4. cleanup 페이지 진화 (D10 후) ⏳

D10 wiki classifier 가 모든 자료 자동 분류한 후 cleanup 페이지의 진화:
- **"wiki 페이지별 자료 부족 정도"** filter 추가
- agent 가 "보강 가치 높음" 마킹한 자료만 필터링
- 사용자가 user_notes 추가하면 → 해당 wiki 페이지 **즉시 재합성**
- cleanup 페이지 = placeholder 자료 viewer + wiki 분류 결과 확인 + 잘못 분류된 자료 사용자 수정 도구

### D12. placeholder / 본문 추출 실패 자료 정리 UI ✅ 완료 (2026-05-25, commit 39a82d1 + aba9f65 + 269b3b5)

**배경**: §2 raw-first 원칙의 확장 — LinkedIn / Facebook / Medium paywall /
이미지 / PDF placeholder 같은 본문 추출이 어려운 자료도 **raw URL + 메타는
무조건 DB 에 등록** + 사용자가 LinkMind UI 에서 수동 보강 (본문/메모/카테고리)
가능해야 한다는 사용자 요구 (2026-05-23, 2026-05-25 raw-first 확장 결정).

#### Wave-1 (commit 39a82d1) — UI + 분류 인프라

**Backend**:
- `backend/jobs/mark_fetch_failed.py` 신규 — `source_metadata.fetch_error_kind`
  표준화. classify_item pure helper + idempotent. **2,594건 자동 마킹**:
  image_no_ocr 1,751 / extraction_failed 806 / binary_no_extract 36 / short_raw 1.
- `backend/api/items.py` 의 list endpoint (`GET /items`) — 필터
  (kind/source_type/domain/has_user_notes/has_summary/q) + pagination + facets
  drilldown (skip_* 로 자기 facet 제외). raw_content 는 preview 400자만.
- POST `/items/{id}/notes` — user_notes append (raw-first §2 보전, 덮어쓰기 X,
  idempotent, LLM 키워드 추출 background task).
- POST `/items/{id}/categories/{slug}` — item 의 첫 topic 을 카테고리에 manual link.

**Frontend** (`frontend/app/cleanup/`):
- FilterSidebar — facet 카운트 + drilldown 필터.
- ItemCard — 이미지 thumbnail 인라인 + tags + URL + fetch_error_message preview.
  kind=image_no_ocr 시 grid 레이아웃 (썸네일 시각 인지 우선).
- ActionPanel — kind별 hint + 원본 새창 + user_notes append textarea + 카테고리
  검색·link + raw preview.
- Header 메뉴 "🧹 정리 / Cleanup" + i18n.

**Tests**: 32개 신규 (cpu): classify_item 11 + _build_where / _extract_domain /
_truncate 21.

#### Wave-2 (commit aba9f65) — Slack manifest 재처리 + provenance 보강

사용자 요구 (2026-05-25): "진짜 데이터 사라진 것 아니면 모두 DB 에 입력해야지" —
Slack ingest 도중 발생한 953 issues (placeholder 633 + exception 320) 를 모두 재처리.

- `backend/db/repository.merge_source_metadata(item_id, extra)` 신규 — jsonb concat
  으로 source_metadata 에 top-level 키 merge.
- `backend/ingest/slack/__init__.py` — URL ingest 분기에서 결과 item_id 로
  `_attach_slack_metadata_to_item` 호출 → source_metadata 에 'slack' 키 보강.
  wave-2 시점부터의 한계 (URL 분기에 slack provenance 누락) 해결.
- `backend/jobs/ingest_slack_manifest.py` 신규 — manifest 재처리 job:
  - placeholder 633: 이미 DB 에 있는 item 에 slack metadata merge only
  - exception 320: wave-5/D13 fix 흐름으로 재시도. 성공 시 정상 ingest, 실패 시
    `_save_url_only` 흐름으로 URL+caption placeholder 저장
  - 결과 추적: `result_manifest.json` (953건 1:1) + `unresolved_manifest.json`
    (안 들어간 38건)
  - `source_metadata.manifest_input_url` 보존 → ingest 가 canonical URL 변환하거나
    hash dedup 으로 기존 item 반환해도 manifest entry ↔ item 역추적 가능.

**실행 결과**:
- merged_only 790 (이미 DB 에 있음, slack metadata 보강)
- retried_success 125 (재시도로 정상 ingest 본문+summary 모두)
- retried_placeholder 37 (외부 사이트 죽음, placeholder)
- skip 1
- **915 / 953 (96%) DB 등록.** unresolved 38건은 YouTube 영상 삭제 37 + 깨진 URL 1.

**Slack 첨부 보존**: 198개 (PDF 48 + 이미지 130 + 영상 13 + 기타) 가 ingest_document
의 SHA-256 dedup 으로 `volumes/archive/` (4.7GB) 에 영구 복사. sample 8/8 통과.
→ `archive/slack_export/` 삭제 안전 (2026-05-25 완료).

**Tests**: 14 케이스 신규 (cpu).

#### Wave-3 (commit 269b3b5) — 영구 삭제 (2단계 confirm)

사용자 발견 (2026-05-25): 진짜 사라진 자료 (YouTube 영상 삭제 / 도메인 죽음 /
HTTP 429 영구 차단 등) 를 cleanup 페이지에서 정리 가능해야. §11 Privacy 원칙
§4 (삭제 권리, GDPR/PIPA) 부합 — §2 raw-first 와 충돌 X (raw-first 는 "ingest
시점 무손실 보존" 의미, 사용자 명시 삭제는 다른 차원).

**Backend**:
- `DELETE /items/{id}` 신규 (backend/api/items.py):
  - Qdrant chunks collection 의 item_id payload 별 points 삭제
    (`delete_chunks_for_item` 기존 helper 재사용 — 이미 존재)
  - Postgres items row DELETE → schema 의 `ON DELETE CASCADE` 가 chunks /
    attachments / item_topics 자동 삭제
  - `volumes/archive` 의 raw 파일은 보존 (SHA-256 dedup 라 다른 item 이 같은
    file_hash 참조 가능 — orphan cleanup 은 별도 job)
  - 404 if 존재 안 함, 200 + `{deleted, item_id, qdrant_status}` if 성공

**Frontend** (`ActionPanel.tsx`):
- 영구 삭제 섹션 신규 (마지막 위치, border-top 으로 시각 분리)
- 1단계: 빨간 outline "🗑 이 자료 삭제..." 버튼
- 2단계: 빨간 경고 박스 — title + URL 링크 + raw_content 첫 200자 + "정말
  삭제하시겠습니까? 이 자료는 영구히 삭제됩니다." + [취소] / [삭제 확정]
- 카드 바뀌면 confirm 상태 자동 reset
- 삭제 후 `onDeleted` callback → 패널 닫기 + list refresh

**Smoke 검증** (실 데이터 `1L_Ll_MtrVs` YouTube 자료):
- 2건 (`&` + `&amp;` 인코딩 중복) DELETE 성공
- items + chunks + attachments + item_topics 모두 CASCADE 로 0건
- Qdrant `status=0 (completed)` — points 정리됨
- 같은 id 재호출 시 404 정상

**추후 (D10 wiki classifier 도입 후)**: wiki 페이지 단위로도 cascade 삭제
흐름 추가 가능.

**핵심 결과**: cleanup 페이지에서 사용자가 user_notes 로 보강한 메모는 D10
llm_wiki 의 multi-agent 가 wiki 페이지 합성 시 중요 신호. raw fetch 실패한
자료도 사용자 메모 풍부하면 wiki 섹션으로 부활. **진짜 사라진 자료는 명시적
영구 삭제** 가능 — wave-3 의 두 단계 confirm UI.

### D13. 임베딩 모델 별도 서버 분리 (vLLM-embed) ⏳ 진행 중 (2026-05-23)

**배경**: Slack ingest 도중 발견된 GPU OOM 문제 (CLAUDE.md §13 Phase C wave-2) 의
근본 해결. bge-m3 가 프로세스마다 (backend uvicorn / watcher / CLI) GPU 에 따로
로드되어 vLLM (qwen2.5-7B, 18.8 GB) 과 함께 24 GB GPU 거의 점유, 추가 프로세스가
OOM. 단기 해결책 (uvicorn stop) 매번 반복은 운영 burden + 실수 위험.

**도구 선택**: vLLM 으로 통일 (2026-05-23 사용자 결정). TEI (HuggingFace) 가
커뮤니티 표준이지만 시기적 우위일 뿐 vLLM 0.6+ 도 embedding 지원. self-host
정체성 (§3) 과 운영 단순성 (LinkMind 의 모든 inference 가 vLLM 하나) 우위.

**현재 진행**: TodoWrite 에서 추적. 변경 범위:
- compose 에 vllm-embed 서비스 추가 (`vllm serve BAAI/bge-m3 --task embed`)
- `backend/embedding/vllm_embed.py` 신규 (OpenAI-compatible `/v1/embeddings` HTTP client)
- `backend/embedding/factory.py` 의 env switch (`EMBEDDING_BACKEND=local|vllm`)
- env / step2_2 script / 단위 테스트
- 반쪽 49건 backfill (`backfill_summary` 에 `_embed_and_index` 추가)
- watcher 재기동 → 텔레그램 채널의 OOM 실패 메시지 자동 backfill

GPU 메모리 예상: vLLM-llm 18.8 GB + vLLM-embed 3.8 GB = 22.6 GB / 24 GB
(여유 1.4 GB). watcher/uvicorn/CLI 는 GPU 안 씀.

### D14. wave-5 보강 (운영 자동화 + Graph fix + Ingest fallback) ✅ 완료 (2026-05-25)

오늘 한 세션에 정리된 운영 안정성 + UX 보강 5종. CLAUDE.md §13 의 "wave-5 보강"
섹션 참고. 핵심:

1. **Telegram watcher batch 구조 제거** — GPU VRAM 한계 (bge-m3) 로 어차피 직렬
   ingest, batch 효익 X. yaml 순서대로 한 채널씩 `[resolve → backfill → 다음]`.
   `[N/M] 채널 처리 시작:` 형식 진척 로그. `batch_size` 필드 silent ignore (backward
   compat).
2. **`ai_agents/check_telegram_invites.py` 신규** — invite 검증 + 자동 정리 (yaml +
   `channel_id_cache.json`, `.bak.<ts>` 백업). 분류: ALIVE / NOT-MEMBER (dialog
   snapshot 검증) / DEAD-HASH / SKIPPED. CLI: `python -m ai_agents.check_telegram_invites`
   (기본 자동 정리) / `--dry-run` (검증만). 17 unit test.
3. **`scripts/step5_run_dev.sh` 통합** — watcher 시작 직전 check 자동 실행
   (`--skip-check` flag 또는 `SKIP_INVITE_CHECK=1`). session lock 충돌 없음 (check
   가 disconnect 후 watcher session 잡음). 검증: yaml 14→6 / cache 9→3 entries.
4. **Graph "node not found" 근본 fix** — `backend/api/graph.py` 3 endpoint
   (`graph_categories` / `graph_search` / `graph_item_neighborhood`) 가
   `list_topics(limit=500)` 으로 fetch 후 dict lookup. DB 의 23,631 topic 중 limit
   밖은 dangling → frontend react-force-graph 가 runtime error. `list_topics_by_ids
   (ids)` 신규로 정확 fetch. smoke: 모든 endpoint dangling=0.
5. **URL ingest OG meta fallback** — `_parse_og_meta` / `_og_as_body` /
   `_body_is_meaningless` 신규. body 추출 실패 (또는 readability 빈 wrapper) 시
   og:title/description/image 로 body 합성. Telegram/Slack 카드와 동일 데이터.
   abstract cutoff 200자 → 100자 (SNS 카드 description). 17 unit test.
6. **YouTube channel handle + oEmbed fallback** — `parse_youtube_url` 에 `channel`
   kind 추가 (`/@username`, `/c/`, `/channel/UC...`, `/user/`). yt-dlp 실패
   (가장 흔한 원인은 IP 차단, "video not available" 위장) 시 oEmbed API (public,
   IP 차단 거의 없음) 로 title + author + thumbnail 보존 + LLM summary/tags. 검증:
   차단됐던 `hYG9uREf4EU` → "CVPR2026 - SV-GS..." title + summary 431자 + tags 10개.
   7 unit test.
7. **fallback topic 테스트 갱신** — wave-3 의 fallback topic (`url:item:<uuid>`)
   동작 반영. 옛 noop 테스트 제거, 신규 2 케이스.

**총 영향**: 327 pytest passed / 0 회귀. 327 = 282 (이전) + 45 (신규 unit test).

**검색 quality 이슈 발견 + D10 이월**: PDF chunk text extraction 실패 (수학식
PDF 의 pypdf garbage) 로 검색 score 가 낮음. summary 는 정상 한국어인데 embed
안 됨. 단기 fix (summary chunk 추가 / PDF re-extract) 는 D10 llm_wiki 가 검색
패턴 재설계 (chunk cosine → wiki 페이지 retriever) 하므로 redundant. memory
[[project-search-quality-issue]] 보존.


---

## D15. 위키/키워드/사진 대정비 ✅ 완료 (2026-05-29) — 한 세션, 다수 commit

D10.6 wiki 중복 fix 로 시작해 키워드 정규화·Settings 연동·사진→위키 figure 연결·
ingest going-forward fix 까지 확장. 모두 사용자 검증 완료.

### D15.1 D10.6 wiki 중복 fix ✅ (생성측 A/A2 + 정리측 B + rehome)
- **A** — classifier (`backend/agents/classifier.py`): topics→wiki 승격에 `it.confidence`
  반영. `_select_identity_topics_for_wiki` — confidence≥0.9 자기 정체성 topic 만 wiki,
  0.7 cross-modal 단서는 관계로만. (옛 코드는 confidence 무시 → 같은 자료가 여러 wiki.)
- **A2** — `backend/utils/external_ids.py` `native_identity_external_id(source_type,url,ids)`:
  정체성을 자료 *자기 타입/URL* 에서만 (youtube→yt, github→github, pdf→arxiv·doi,
  url/메모→자기 URL). 콘텐츠(설명란/README) 링크는 0.7 관계. 진짜 근본 원인 = 옛
  `primary_external_id` 가 source_type 무시 + 고정순위라 youtube 설명란 github 가 정체성
  가로챔. `auto_link_topics` 가 url= 받고 primary=None 일 때도 본문 link 0.7 보존.
- **B** — `backend/jobs/cleanup_duplicate_wikis.py` (native-identity 기준):
  T1 self_wiki(url__item__) 가 native 외부 wiki 도 가지면 merge (1,591). T2 외부 prefix
  인데 아무 item 도 native 소유 안 함(phantom) 삭제 (8,660). item_topics·agent_runs 보존.
  `--dry-run`/`--t1-only`/`--t2-only`/`--rehome-orphans`. 25,621 → ~15k wiki.
- **rehome** — cleanup 후 wiki 없어진 item 에 self_wiki 보장 (idempotent §2: DB item 은
  전부 비중복 → 모두 정체성 wiki 보유). `--rehome-orphans`.
- 진단 정정: 원래 메모의 confidence 방향/"같은 item primary 공유 grouping" 이 틀림.
  [[project-wiki-dedup-diagnosis]].

### D15.2 키워드 정규화 + Settings/DB 연동 ✅
- `backend/utils/keywords.py` — `normalize_keyword` : **영문 only**(CJK/한글/일본어 삭제) +
  소문자-대시 slug (camelCase·약어 분리: TreeAIBox→tree-ai-box) + **알려진 약어**
  (LiDAR→lidar, GitHub→github, IoT→iot, CMake→cmake …) + **별칭**
  (3d-gaussian-splatting/gaussian-splatting→3dgs) + dedup. `set_keyword_config` 런타임.
- **Settings 페이지 + DB 연동**: app_settings `keyword_acronyms`/`keyword_aliases` (텍스트),
  `runtime_settings.update_keyword_config`, API `GET/PUT /settings/keywords` +
  `POST /settings/keywords/reapply`. frontend Settings 에 '🔤 키워드 정규화' 섹션
  (약어/별칭 textarea + 저장 + 기존 데이터에 적용). **앞으로 약어는 사용자가 직접 추가.**
- backfill `backend/jobs/normalize_keywords.py` (전체 재정규화, reapply_all 공용).
- `_SEARCH_KEYWORDS_SQL` garbage 필터('---' 등 제외).

### D15.3 writer max_tokens 1024 → 2048 ✅
- 한국어 wiki 가 completion 1024 에서 잘려 **마지막 ## Keywords 섹션 누락** → 키워드 0.
  2048 로 7 섹션+Keywords 완주. 옛 1024 합성 wiki 다수 키워드 잘림 — 재합성 시 복구.

### D15.4 키워드 cloud UI + wiki list 정렬/필터 ✅ (`frontend/app/wiki/page.tsx`)
- 좌측 사이드바 키워드 cloud — 빈도순, '더 보기'(점진 로드)/'전부'/'접기', 검색창
  (빈도 무관 전체 접근), 파랑 색상, ☆/⭐ 즐겨찾기 마크.
- wiki list 정렬 select (날짜순 최신/오래된 · 가나다 오름/내림), 다중 키워드 **AND** 필터
  (`?keyword=A&keyword=B`, 카드 pill·필터 chip 토글).

### D15.5 사진 → 위키 figure 연결 + ingest going-forward ✅
- 문제: 텔레그램 '사진+URL캡션' 한 메시지가 사진/URL 2 item 으로 쪼개져 사진이 고아
  photo 위키(self_wiki) 가 됨 (1,817건).
- `backend/jobs/link_photo_captions.py` — caption URL 역추적(`first_url`+external_id 정규화)
  → 그 URL 의 위키에 **figure 소스로 link** (1,645) + 단독 photo(스크린샷 중복) item+wiki
  삭제 (172, 이미지는 volumes 보존). `--delete-standalone`.
- 뷰어 fix: wiki Sources 의 `/files/<hash>` 이미지를 절대경로(:8000) inline 표시 (옛 상대경로
  → :3001 404).
- **going-forward**: (A) telegram ingest — URL 없는 단독 첨부(사진) skip → 텔레그램 잔류
  (`is_ingest_successful=False` 라 미삭제). (B) classifier — 사진+URL캡션 → caption 위키에
  figure link + self_wiki 스킵 (위키 미존재 시 self fallback → 주기 link_photo_captions 보정).

### 검증
- cpu 테스트 493 passed. frontend tsc OK. 사용자 라이브 검증 (사진+URL → figure, 사진만 → 잔류).

---

## ✅ 2026-05-29 후반 — D10.5 A+B + 그래프 페이지 제거 + /wiki 우측 패널 (완료)

- **D10.5 세션 A** — graph item 클릭 → 우측 wiki body inline. `GET /items/{id}` 에 wikis 조인.
- **D10.5 세션 B** — 그래프 `keyword ▸ wiki ▸ item` 재구성 (옛 category/topic 폐기, `wiki_pages.keywords`
  로 그룹 통일) + **실시간 co-occurrence** (`/graph/keyword/{kw}` — 같은 위키 공유 키워드, GIN, 동적).
  `backend/api/graph.py` 재작성 + repository 5 함수 + frontend 전 컴포넌트 전환.
- **그래프 페이지 메인 nav 제거** (사용자 결정) — 키워드 49,919 규모에 force-graph 효용 낮음
  (점구름·클릭지옥). 홈 `/`→`/ask` redirect. 옛 페이지 → `app/graph/page.tsx` 보류 (URL 직접 접근만).
  컴포넌트 + backend `/graph/*` endpoint 는 남김 (본격화/삭제 추후 결정).
- **/wiki 리스트 → 우측 패널 inline 상세** — 카드 클릭 시 페이지 이동 X.
  `components/wiki/WikiDetailView.tsx` 추출 (`variant` page/panel, /wiki/[slug] 공용 — 편집/재합성/
  Sources/Relationship/Keywords/메타/2단계 삭제 전부). 패널 폭 58rem, 전체 펼침(자체 스크롤 X).

## ✅ 2026-05-30 — Gemma 4 전환 + tags 폐기 + summary 한국어 + vLLM DB화 (완료, 텔레그램 실시간 검증)

- **Qwen → Gemma 4 26B-A4B (MoE, AWQ 4bit)** — 중국어 native bias 근본 제거. vLLM KV cache fp8 +
  16384 ctx + reasoning-parser gemma4 + 멀티모달 끄기(`--limit-mm-per-prompt`) (RTX 4090). LoRA 는
  Gemma MoE 미지원이라 제거. writer 풍부화 (max_tokens 6144, source top-8).
- **위키 title/description 정제** — raw SNS 제목/중국어 → body 의 `#헤더`(정제 제목)·`>TL;DR`. writer
  저장 + `backfill_description_from_tldr` (title 1208 + desc 14181).
- **tags 완전 폐기** — #tag 검색(/search 폐기) + 위키 키워드 대체. summary 프롬프트 해시태그 제거,
  ingest/writer/retriever/frontend tags 제거, items.tags 비움(14443) + summary 해시태그 줄 제거(12506).
- **summary 한국어화** — `backfill_summary --only-foreign` (중국어만). 13839 중 중국어 0.
- **vLLM 설정 DB화** — model/dtype/gpu_mem/max_len/kv_cache 를 app_settings + Settings UI. `vllm_restart.sh`
  (=vllm_restart.py) 가 DB effective 읽어 재구동(shell env > --env-file). env VLLM_* 제거 (config fallback).
- **위키 UI** — 날짜 정렬 `COALESCE(updated_at,created_at)`, 갱신날짜 색상 통일, 자료별 Summary 섹션
  제거(위키 요약=본문 TL;DR), 헤더 모델명 동적(`ModelLabel` → getLLMSettings).
- **watcher fix** — `setup()` seed_and_load (DB vllm_model 적재, 옛 Qwen 404 해결). 별 프로세스
  (watcher/backfill/CLI)는 seed_and_load 필수 패턴.
- 신규: `backend/utils/lang.py`(외국어 감지), `regenerate_foreign_wikis`, `backfill_description_from_tldr`,
  `vllm_restart`, `run_summary_backfill --only-foreign`, `ModelLabel.tsx`. 테스트 `test_lang_foreign` 8개.

## 🎯 다음 세션 — 여기부터 (간단명료)

> Gemma 전환·tags 폐기·vLLM DB화 끝. 홈 = `/ask`. 이제:

**1순위 — 대화형 /ask (멀티턴 + 검색 + agentic action)**
- 현재 `frontend/app/ask/page.tsx` 는 1-shot RAG (Step 1). → **멀티턴 대화 UI** + 세 요청 유형:
  · 검색("OO 자료 찾아줘") · QA("OO 할 땐 어떻게 해?") · **agentic action**("위키 링크 관계를 로컬
    데이터로 업데이트해줘" → 도구 호출 실행, 사용자 지시 기반).
- 대화 history + streaming + citation/related_wikis + 우측 wiki inline (`WikiDetailView` 재사용).
- 같이: **ask·검색을 wiki body 기반으로** (item.summary 의존 줄이기). `search_wiki_pages`(wiki_qdrant)
  + `POST /wiki/search` 이미 구현 → 재사용.

**2순위 — 자가학습 (auto-skills) + 학습 파이프라인 (Phase 5)**
- **자가학습**: feedback(👍/👎) 누적 → prompt/ingester 자동 개선 (사용자 명령 없이, 자동).
  ※ "위키 링크 업데이트해줘"는 자가학습 아니라 ①의 agentic action(사용자 지시).
- **학습**: feedback 인프라 (👍/👎/수정 → feedback 테이블) → dataset exporter (raw + summary +
  user_notes + feedback → JSONL) → sVLL LoRA (LLaMA-Factory + Qwen2-VL 또는 Gemma 4)

### Phase 5 학습 파이프라인 — Gemma 4 26B-A4B QLoRA (RTX 4090 24GB)

> 핵심 원칙: **운영 = AWQ(inference 전용), 학습 = 원본 + QLoRA** 로 분리. 별도 conda env(§4 — 학습용은
> 그 시점에 생성). personal LoRA = 본인 데이터로 본인 모델만 (§1·§11, 운영자 공통모델 학습 절대 X).

**1) 학습 (QLoRA, 24GB)**
- 원본 `google/gemma-4-26B-A4B-it` (bf16, HF gated — 라이센스 동의 + HF_TOKEN) → **QLoRA**:
  4bit NF4 로 base 로드 + LoRA adapter 만 학습. **운영 중인 AWQ 버전(cyankiwi/...-AWQ-4bit)은
  inference 전용이라 직접 학습 불가** → 원본을 받아야 함.
- **24GB 가능**: QLoRA 논문 기준 33B@24GB / 65B@48GB → 26B 는 여유. 단 조건:
  paged AdamW(8bit) + **gradient checkpointing** + batch 1 + seq 1024~2048 제한 + grad accumulation.
  MoE 라 weights 26B 전체를 4bit 로 로드(~13GB)하지만 연산은 active 4B.
- **MoE LoRA 변수 (검증 필요)**: LLaMA-Factory / PEFT 가 Gemma 4 MoE 의 **expert layer 에 LoRA**
  를 제대로 붙이는지. attention 만 target = 안전, expert 까지 target = 지원 확인 필요. 안 되면
  더 작은 변형 **Gemma 4 E4B**(학습 쉬움) 또는 클라우드 GPU 학습으로 우회.

**2) 데이터셋**
- dataset exporter: raw_content + summary + user_notes + feedback(👍/👎/수정) → JSONL (instruction 형식).
- Gemma 4 멀티모달 → **이미지 자료(PDF figure / 사진 / thumbnail)도 input** (§1 sVLL 비전과 직결).

**3) 추론 최적화 (학습 후)**
- LoRA adapter → **base 에 merge** → **AWQ 재양자화**(autoawq) → vLLM 서빙 (현재 운영 파이프 그대로).
- **merge 필수**: vLLM 이 Gemma 4 **MoE 의 inference LoRA hot-swap 을 미지원**(2026-05-30 확인 —
  `get_expert_mapping must be implemented`). adapter 를 따로 못 올리므로 merge 후 통째 재양자화.

**4) continuous training loop (Phase 6)**
- 주기적으로 feedback 누적 → QLoRA 재학습 → merge/양자화 → 재배포. 온프레미스 개인화 엔진 완성.

**그 외 / 보류**
- `/graph` 페이지 본격화할지 완전 삭제할지 결정 (현재 nav 제거 + 코드 보류)
- D10 wave-3 critic agent / D8 cross-modality / D10 lint job / link_photo_captions 자동화

> 운영: `bash scripts/step5_run_dev.sh` 전체 기동. 키워드/약어는 **Settings 페이지**에서 편집.
