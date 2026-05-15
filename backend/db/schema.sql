-- ============================================================================
-- LinkMind — 초기 DB 스키마
-- ----------------------------------------------------------------------------
-- 설계 원칙 (project-linkmind-training-goal.md):
--   1) Raw-first   : raw_content를 NOT NULL로 강제. 분석/요약은 부가 정보.
--   2) Provenance  : source_type/source_url/source_id/content_hash 등 출처 추적.
--   3) Idempotent  : (source_type, content_hash) UNIQUE → 동일 자료 재수집 방지.
--   4) Versioned   : 모든 AI 분석 결과에 model/prompt 버전 기록.
--   5) Loss-less   : 첨부(이미지/PDF)는 attachments 테이블로 별도 보존.
--
-- 이 파일은 Docker Postgres 컨테이너 첫 부팅 시 자동 실행됨.
-- (compose/docker-compose.dev.yml 의 entrypoint mount 참조)
-- ============================================================================

-- ── Extensions ────────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS "pgcrypto";   -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS "pg_trgm";    -- 부분일치 검색용
-- pgvector는 사용하지 않음. 벡터 검색은 Qdrant에 위임.


-- ============================================================================
-- items : 모든 수집 자료의 기본 단위
-- ============================================================================
CREATE TABLE IF NOT EXISTS items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- ── Provenance (sVLL 학습용 필수 메타데이터) ──
    source_type      TEXT NOT NULL,         -- 'slack' | 'telegram' | 'url' | 'pdf'
                                            -- 'github' | 'arxiv' | 'youtube' | 'manual'
    source_id        TEXT,                  -- platform-specific id (slack ts, msg_id, url, doi, …)
    source_url       TEXT,
    source_metadata  JSONB DEFAULT '{}'::jsonb,

    -- ── Raw content (loss-less, 절대 변형 금지) ──
    raw_content      TEXT NOT NULL,         -- 원본 텍스트
    raw_content_hash TEXT NOT NULL,         -- SHA-256(raw_content) → idempotent 키
    raw_file_path    TEXT,                  -- 원본 파일이 있으면 storage 경로
    raw_mime_type    TEXT,
    raw_bytes        BIGINT,

    -- ── Derived/analysis (재생성 가능, 모델 버전 추적) ──
    title                  TEXT,
    summary                TEXT,
    summary_model          TEXT,            -- e.g. 'gpt-4o-mini'
    summary_prompt_version TEXT,            -- e.g. 'v1'
    summary_generated_at   TIMESTAMPTZ,

    categories       TEXT[] DEFAULT '{}',   -- AI 분류 (SLAM, 3DGS, …)
    tags             TEXT[] DEFAULT '{}',
    language         TEXT,

    -- ── Timestamps ──
    source_created_at TIMESTAMPTZ,          -- 원본 자료의 생성 시각 (있으면)
    ingested_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- ── Full-text search (검색 fallback / hybrid retrieval용) ──
    fts_vector tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('simple', coalesce(title,       '')), 'A') ||
        setweight(to_tsvector('simple', coalesce(summary,     '')), 'B') ||
        setweight(to_tsvector('simple', coalesce(raw_content, '')), 'C')
    ) STORED,

    UNIQUE (source_type, raw_content_hash)
);

CREATE INDEX IF NOT EXISTS idx_items_source        ON items (source_type, source_id);
CREATE INDEX IF NOT EXISTS idx_items_ingested_desc ON items (ingested_at DESC);
CREATE INDEX IF NOT EXISTS idx_items_categories    ON items USING GIN (categories);
CREATE INDEX IF NOT EXISTS idx_items_tags          ON items USING GIN (tags);
CREATE INDEX IF NOT EXISTS idx_items_fts           ON items USING GIN (fts_vector);
CREATE INDEX IF NOT EXISTS idx_items_meta          ON items USING GIN (source_metadata);


-- ============================================================================
-- chunks : items.raw_content를 임베딩 단위로 분할.
-- Qdrant의 point와 1:1 대응 (qdrant_point_id = chunks.id 권장).
-- ============================================================================
CREATE TABLE IF NOT EXISTS chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    item_id UUID NOT NULL REFERENCES items(id) ON DELETE CASCADE,

    chunk_index  INTEGER NOT NULL,          -- item 내 chunk 순서 (0-based)
    chunk_text   TEXT NOT NULL,
    chunk_tokens INTEGER,

    -- 어떤 모델로 임베딩했는지 (재임베딩/모델 교체 추적)
    embedding_model   TEXT NOT NULL,        -- e.g. 'BAAI/bge-m3'
    embedding_version TEXT,                 -- 모델 weight 버전/해시
    embedding_dim     INTEGER,

    -- Qdrant 쪽 식별자 (보통 chunks.id와 동일하게 사용)
    qdrant_point_id   TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (item_id, chunk_index, embedding_model)
);

CREATE INDEX IF NOT EXISTS idx_chunks_item ON chunks (item_id);
CREATE INDEX IF NOT EXISTS idx_chunks_model ON chunks (embedding_model);


-- ============================================================================
-- attachments : 이미지/PDF 등 멀티모달 자산.
-- VLM 학습 시 (image, caption/description) 페어로 사용됨.
-- ============================================================================
CREATE TABLE IF NOT EXISTS attachments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    item_id UUID NOT NULL REFERENCES items(id) ON DELETE CASCADE,

    file_path  TEXT NOT NULL,               -- storage 상의 경로 (local 또는 minio key)
    mime_type  TEXT,
    file_size  BIGINT,
    file_hash  TEXT NOT NULL,               -- SHA-256

    -- VLM 학습용 메타데이터
    role            TEXT,                   -- 'figure' | 'diagram' | 'screenshot' | 'photo' | 'attachment'
    caption         TEXT,                   -- 원본 caption (예: 논문 figure caption)
    ai_description  TEXT,                   -- AI가 생성한 설명
    ai_description_model TEXT,

    width   INTEGER,
    height  INTEGER,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (item_id, file_hash)
);

CREATE INDEX IF NOT EXISTS idx_attachments_item ON attachments (item_id);
CREATE INDEX IF NOT EXISTS idx_attachments_role ON attachments (role);


-- ============================================================================
-- ingestion_runs : 수집 배치의 실행 로그. Slack export 같은 bulk job 디버깅용.
-- ============================================================================
CREATE TABLE IF NOT EXISTS ingestion_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_type TEXT NOT NULL,
    status      TEXT NOT NULL,              -- 'running' | 'completed' | 'failed'

    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,

    items_added   INTEGER DEFAULT 0,
    items_skipped INTEGER DEFAULT 0,
    items_failed  INTEGER DEFAULT 0,

    config JSONB DEFAULT '{}'::jsonb,
    error  TEXT
);

CREATE INDEX IF NOT EXISTS idx_ingestion_runs_started ON ingestion_runs (started_at DESC);
CREATE INDEX IF NOT EXISTS idx_ingestion_runs_source  ON ingestion_runs (source_type, status);


-- ============================================================================
-- app_settings : 런타임에서 UI/API 로 변경 가능한 key-value 설정.
-- LLM provider/model 의 "기본값" override 보관 (env/dev.env 는 시드값, 여기가 우선).
-- value 는 JSON 문자열 또는 단일 문자열 — 단순 TEXT 로.
-- ============================================================================
CREATE TABLE IF NOT EXISTS app_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- ============================================================================
-- prompts : 시스템 프롬프트 버전 히스토리 (Versioned analysis 원칙).
-- 새 버전 저장 시 기존 버전은 그대로 보존 — 학습 데이터 재현/감사용.
-- (name) 당 is_active=TRUE 행은 정확히 1개 (partial unique index).
-- ============================================================================
CREATE TABLE IF NOT EXISTS prompts (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name       TEXT NOT NULL,              -- 'rag_system' | 'summary_system' | ...
    version    TEXT NOT NULL,              -- 'v1', 'v2', ... — name 안에서 단조 증가
    content    TEXT NOT NULL,
    is_active  BOOLEAN NOT NULL DEFAULT FALSE,
    note       TEXT,                       -- 변경 사유 (선택)
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (name, version)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_prompts_active_per_name
    ON prompts (name)
    WHERE is_active;

CREATE INDEX IF NOT EXISTS idx_prompts_name_created ON prompts (name, created_at DESC);


-- ============================================================================
-- Trigger: items.updated_at 자동 갱신
-- ============================================================================
CREATE OR REPLACE FUNCTION trg_set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS items_set_updated_at ON items;
CREATE TRIGGER items_set_updated_at
    BEFORE UPDATE ON items
    FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();
