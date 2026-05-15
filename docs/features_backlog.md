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
- `scripts/backfill_external_ids.py` — 기존 item 들에 소급 적용 (`source_metadata.external_ids` 키 유무로 idempotent, `--force` 재계산)

### 2.5.2 — API / UI ✅

- `GET /topics?limit=N` — 최신 updated 순 + item_count
- `GET /topics/{id_or_slug}` — 상세 + 그 안의 모든 item (role 정렬). slug 안의 '/' 도 처리 (`{slug:path}`)
- `GET /topics/items/{item_id}` — item 의 topic membership (검색 결과 보강)
- `POST /topics/items/{id}/link` — 수동 link (source='manual')
- Streamlit **Topics 탭** 신규: 왼쪽 topic 목록 ↔ 오른쪽 상세 (description / items role 별)
- Search 결과 보강: hit 별로 `📚 topics: slug(role) ...` 칩 표시
- 수동 link UI (item_id + slug + role + note)

### 2.5.3 — Topic description 자동 생성 ✅

- `scripts/generate_topic_descriptions.py` — 자식 item 2개 이상인 topic 에 대해 (role, title, summary) 합쳐 LLM 으로 한국어 5-8 bullet 합성 → `topics.description`
- `_TOPIC_SYSTEM_PROMPT` 별도 — "같은 주제를 여러 modality 가 어떤 관점에서 다루는지" 명시
- 검증: arxiv:2106.09685 (LoRA paper + GitHub) + arxiv:2511.20343 (AMB3R paper + GitHub + project page)

### 2.5.4 — 실 데이터 검증 ✅

- arxiv:2106.09685 — LoRA paper URL + microsoft/LoRA GitHub → 같은 topic 자동 묶임
- arxiv:2511.20343 — AMB3R paper + HengyiWang/amb3r + project page (hengyiwang.github.io) 3 modality 자동 묶임 + Livioni/OmniVGGT 는 별 topic (false positive 없음)
- microsoft/LoRA README 의 paper link 6개 → arxiv:1907.11692/2006.03654/1902.00751/2101.00190 + huggingface/peft 등 6개 secondary topic 자동 생성

---

## Phase 2.5 다음 wave (선택, 미착수)

- 검색 결과에 같은 topic 안의 다른 item 도 자동 인용 (현재는 칩만 표시, 클릭하면 topic 상세)
- arxiv API 시드 — paper_id 만 있는 topic 의 title/author/published_at 자동 보강
- paperswithcode slug → github_repo 자동 연결 (현재는 README 에 링크 있어야)
- Streamlit 의 manual link UI 에서 search-by-slug autocomplete

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

## Phase C — Slack / Telegram / Phase 2-3 본격

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
