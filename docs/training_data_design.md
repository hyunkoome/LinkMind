# 학습 데이터 설계 (sVLL 파인튜닝 준비)

> **상위 목표**: LinkMind 에 누적되는 개인 데이터로 **sVLL(small Vision-Language LLM)** 을 파인튜닝해
> 온프레미스 personalized AI 엔진을 만든다. LinkMind 는 수단, sVLL 엔진이 최종 산출물.
> 따라서 **데이터 레이어 설계가 최우선 가치**다.

---

## 1. 데이터 5대 원칙 (Phase 1 부터 강제)

| # | 원칙 | 강제 위치 | 위반 시 영향 |
|---|---|---|---|
| 1 | **Raw-first** : 원본 텍스트/파일을 무손실 보존 | `items.raw_content NOT NULL`, `attachments.file_path NOT NULL` | 학습 시 복구 불가 |
| 2 | **Provenance** : 출처 모든 메타 추적 | `source_type / source_url / source_id / raw_content_hash / source_created_at` | quality control 불가 |
| 3 | **Idempotent** : 동일 자료 중복 저장 금지 | `UNIQUE (source_type, raw_content_hash)` | 학습 데이터 편향 |
| 4 | **Versioned analysis** : AI 분석 결과는 모델/프롬프트 버전과 함께 | `summary_model / summary_prompt_version`, `embedding_model / embedding_version` | 재학습/A-B 비교 불가 |
| 5 | **Loss-less storage** : 이미지/figure/PDF 변형 없이 보존 | `storage_local_path`, `attachments.file_hash` (resize/compress 금지) | VLM 학습 시 해상도 손실 |

각 원칙은 **DB 스키마 레벨**(NOT NULL, UNIQUE) 과 **ingestion 파이프라인 레벨** 두 군데서 강제한다.

## 2. 데이터 모델 요약

```
items                 # 1 자료 = 1 row
├─ raw_content        # 원본 텍스트 (NOT NULL)
├─ raw_file_path?     # 원본 파일 경로 (storage 상)
├─ raw_content_hash   # idempotent 키
├─ source_*           # provenance
├─ summary / categories / tags    # AI 분석 결과 (재생성 가능)
└─ summary_model / summary_prompt_version  # 분석 버전

chunks                # embedding 단위 (1 item = N chunks)
├─ chunk_text
├─ embedding_model    # 모델 변경 추적
└─ qdrant_point_id    # 벡터는 Qdrant 에

attachments           # 이미지/PDF/figure (VLM 학습 페어용)
├─ file_path / file_hash / mime_type
├─ role               # 'figure' | 'diagram' | 'screenshot' | ...
├─ caption            # 원본 caption
└─ ai_description     # AI 생성 설명 (caption 비어있을 때 학습용 보조)

ingestion_runs        # bulk import 디버깅용
```

자세한 SQL: [backend/db/schema.sql](../backend/db/schema.sql)

## 3. 학습 데이터셋 export (Phase 2)

LinkMind 가 누적한 데이터를 **HuggingFace `datasets` 호환 포맷**으로 export 한다. 위치: `backend/datasets/exporter.py` (Phase 2 신규).

### 출력 포맷

1. **Text instruction (.jsonl)** — LLM fine-tuning 용
   ```json
   {"instruction": "Summarize this technical content.", "input": "<raw_content>", "output": "<summary>", "meta": {"item_id": "...", "model_tag": "gpt-4o-mini", "prompt_version": "v1"}}
   ```

2. **Chat format (.jsonl)** — SFT / DPO 용
   ```json
   {"messages": [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}], "meta": {...}}
   ```

3. **Image-Text pair (parquet)** — VLM fine-tuning 용
   ```
   image_path | caption | ai_description | source_url | item_id
   ```

4. **Q&A from feedback (.jsonl)** — DPO / KTO 용 (Phase 2 `feedback` 테이블 활성화 후)
   ```json
   {"prompt": "...", "chosen": "<good answer>", "rejected": "<bad answer>", "meta": {...}}
   ```

### Export CLI (계획)

```bash
python -m backend.datasets.exporter \
    --format chat-jsonl \
    --since 2026-01-01 \
    --categories SLAM,3DGS \
    --out volumes/datasets/v1/train.jsonl
```

## 4. 파인튜닝 도구 후보 (Phase 3)

RTX 4090 단일 GPU 기준.

| 도구 | 강점 | 비고 |
|---|---|---|
| **LLaMA-Factory** | UI/CLI 둘 다 좋음, VLM 학습 정식 지원 (Qwen2-VL, MiniCPM-V) | 1순위 추천 |
| **unsloth** | 7B 모델 LoRA 가 2배 빠름 | text-only 가 강점, VLM 은 제한적 |
| **axolotl** | config 기반, 재현성 좋음 | 학습 곡선 살짝 가파름 |
| **swift** (Alibaba) | Qwen 계열 VLM 강함 | 한국어 자료 적음 |

대상 모델 후보 (RTX 4090 24GB 에서 LoRA 가능):
- **Qwen2-VL-2B-Instruct** (이미지 OCR 강함, 한국어 OK)
- **MiniCPM-V-2.6** (효율 좋음)
- **LLaVA-OneVision-0.5B/7B** (range 넓음)
- **InternVL2-2B/4B** (figure 이해 강함)

## 5. Continuous training loop (Phase 4+)

```
[ingest] 새 자료 누적
   ↓
[export] 자동 큐레이션 (cron 또는 N건 누적 시)
   ↓
[fine-tune] LoRA 학습 (RTX 4090)
   ↓
[eval] held-out set 으로 자동 평가
   ↓ (통과 시)
[deploy] vLLM / Ollama 에 등록 → LLMProvider 새 항목으로 추가
   ↓
[feedback] 사용자 thumbs up/down → feedback 테이블
   ↓ (다시 export 단계로)
```

각 단계는 LinkMind 의 `scripts/train/*.py` 와 `scripts/eval/*.py` 로 자동화. 별도 오케스트레이션 도구(prefect, airflow) 는 도입 안 함 — cron + 단순 Python 스크립트로 충분.

## 6. 데이터 품질 관리

- **dedup** : raw_content_hash 로 이미 강제
- **noise filter** : 너무 짧은 텍스트 (< N chars), 깨진 PDF OCR 등은 `ingestion_runs.items_skipped` 로 기록
- **labeling UI** : Phase 2 Streamlit 에 "이 자료는 학습에 쓸 만한가?" Y/N 토글 추가 → `items.metadata` 의 `training_eligible` 필드
- **golden set** : 사용자가 직접 선정한 ground-truth 자료를 별도 태그 (`gold` 태그) → eval set 시드

## 7. 보안 / 프라이버시

- 모든 학습 데이터는 **로컬 + 온프레미스** 에 머묾. 외부 API 로 보내는 건 분석 단계의 LLM 호출뿐.
- 향후 sVLL 엔진이 자체 분석을 대체하면 외부 의존도 0 으로 수렴.
- API 키, 토큰은 `env/dev.env` 로만 관리, 절대 학습 데이터에 leak 되지 않도록 추출 단계에서 필터 (Phase 2).
