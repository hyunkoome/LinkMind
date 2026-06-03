"""
LinkMind 전역 설정.

모든 환경변수는 env/dev.env(또는 prod.env)에서 로드되며, Pydantic Settings로
타입 안전하게 관리한다. 코드 어디서도 os.environ 직접 접근 금지.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# 프로젝트 루트 (이 파일 기준 두 단계 위)
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """LinkMind 런타임 설정.

    환경변수 로드 우선순위:
      1) 실제 환경변수 (export ...)
      2) env/dev.env  (.env 파일)
    """

    model_config = SettingsConfigDict(
        env_file=[
            str(PROJECT_ROOT / "env" / "dev.env"),
            str(PROJECT_ROOT / ".env"),
        ],
        env_file_encoding="utf-8",
        extra="ignore",                  # 알 수 없는 env 무시 (compose에서 주입되는 값 등)
        case_sensitive=False,
    )

    # ─── App ──────────────────────────────────────────────────────
    linkmind_host: str = Field(default="0.0.0.0")
    linkmind_port: int = Field(default=8000)
    linkmind_log_level: str = Field(default="INFO")
    linkmind_api_key: str = Field(default="")          # 외부 client 인증용 (비어있으면 미사용)

    # ─── Auth / Multitenant (멀티테넌트 — 인증 + space 격리) ──────
    # JWT 는 httpOnly 쿠키로만 전달 (XSS 안전, 2026-06-03 결정). payload = {sub: user_id,
    # space: active_space_id, exp}. secret 은 반드시 env 로 (코드에 하드코딩 금지).
    # 단일 self-host 라도 로그인 강제 — 기본 user/space 를 lifespan 에서 seed.
    linkmind_jwt_secret: str = Field(default="dev-insecure-change-me")  # 운영은 env 필수
    linkmind_jwt_algorithm: str = Field(default="HS256")
    linkmind_jwt_expiration_hours: int = Field(default=720)            # 30일
    # 쿠키 속성 — self-host(http) 는 secure=False, SaaS(https) 는 env 로 True.
    linkmind_cookie_name: str = Field(default="linkmind_token")
    linkmind_cookie_secure: bool = Field(default=False)
    linkmind_cookie_samesite: Literal["lax", "strict", "none"] = Field(default="lax")
    # frontend origin (CORS + credentials 쿠키). 콤마 구분. credentials 쿠키는 '*' 와 못 씀.
    # 데스크탑 앱/다른 도메인 서버면 그 origin 을 여기에 추가.
    linkmind_cors_origins: str = Field(
        default="http://localhost:3001,http://127.0.0.1:3001"
    )
    # 첫 관리자/조직은 자동 seed 하지 않는다 — 설치 후 user 0명이면 POST /auth/bootstrap
    # (브라우저 /login '조직 만들기')으로 고객 조직이 직접 만든다 (운영자는 인프라만).

    # ─── Database ─────────────────────────────────────────────────
    # 컨테이너에서 돌릴 때는 DATABASE_URL, 로컬에서 돌릴 때는 DATABASE_URL_LOCAL을 우선 사용.
    # FastAPI를 호스트에서 띄우면 host='postgres'가 해석 안 되므로 LOCAL 우선 정책을 settings에서 처리.
    database_url: str = Field(default="postgresql+asyncpg://linkmind:changeme@postgres:5432/linkmind")
    database_url_local: str = Field(default="postgresql+asyncpg://linkmind:changeme@localhost:5432/linkmind")

    # ─── Qdrant ───────────────────────────────────────────────────
    qdrant_url: str = Field(default="http://qdrant:6333")
    qdrant_url_local: str = Field(default="http://localhost:6333")
    qdrant_collection: str = Field(default="linkmind_items")

    # ─── Embedding ────────────────────────────────────────────────
    # backend 선택:
    #   - local : sentence-transformers 가 같은 프로세스 GPU 로드 (MVP 기본).
    #             단 watcher / uvicorn / CLI 가 따로 도는 환경에서 같은 GPU 점유 →
    #             OOM 위험 (CLAUDE.md §13 D13 의 Slack ingest 시 발견).
    #   - vllm  : 별도 vllm-embed 컨테이너 (--runner pooling). 모든 프로세스가 HTTP
    #             공유 → GPU 에 모델 1번만. 권장 운영 모드.
    #   - tei   : HuggingFace Text Embeddings Inference. 도입 안 함 (vllm 으로 통일).
    #   - ollama: Phase 2 후보.
    embedding_backend: Literal["local", "vllm", "tei", "ollama"] = Field(default="local")
    embedding_model: str = Field(default="BAAI/bge-m3")
    embedding_dim: int = Field(default=1024)
    tei_url: str = Field(default="http://tei:80")
    # vllm-embed (CLAUDE.md §13 D13) — `/v1` 포함, 기존 vllm_base_url 패턴 일관.
    vllm_embed_base_url: str = Field(default="http://vllm-embed:8000/v1")
    vllm_embed_base_url_local: str = Field(default="http://localhost:8002/v1")
    # 모델 캐시 받힌 후 True 로 두면 HF Hub metadata HEAD 요청 + 토큰 경고 모두 차단.
    # get_settings() 가 이 값을 보고 process env (HF_HUB_OFFLINE, TRANSFORMERS_OFFLINE)
    # 를 setdefault 로 export 해야 sentence-transformers/huggingface_hub 가 효과 봄.
    hf_hub_offline: bool = Field(default=False)

    # ─── LLM Providers ────────────────────────────────────────────
    # 정책:
    #   - 인프라 위치 (ollama_base_url) / 시크릿 (*_api_key) 만 env 에서 읽음.
    #   - 런타임 선호 (어떤 provider / 어떤 모델) 는 DB 의 app_settings + UI Settings
    #     탭에서 관리. 아래 *_model / default_llm_provider 의 Field default 는 DB 가
    #     비어 있을 때만 사용되는 fallback (이전에는 env 도 override 했지만 dead).
    #   - DEFAULT_LLM_MODEL env 는 제거됨 (provider 별 *_model 이 있어 의미 모호).
    default_llm_provider: Literal["openai", "claude", "ollama", "vllm"] = Field(default="vllm")

    openai_api_key: str = Field(default="")
    openai_model: str = Field(default="gpt-4o-mini")

    anthropic_api_key: str = Field(default="")
    anthropic_model: str = Field(default="claude-haiku-4-5-20251001")

    openrouter_api_key: str = Field(default="")
    openrouter_model: str = Field(default="")

    # GitHub API 인증 — token 있으면 5000/hour, 없으면 60/hour (rate limit 큰 차이).
    # https://github.com/settings/tokens 에서 personal access token (no scope 또는
    # public_repo) 발급.
    github_token: str = Field(default="")

    ollama_base_url: str = Field(default="http://ollama:11434")
    ollama_base_url_local: str = Field(default="http://localhost:11434")
    ollama_model: str = Field(default="qwen2.5:7b")

    # vLLM (Phase 3+) — OpenAI 호환, Ollama 대비 2-10x throughput.
    # docker compose 의 vllm service (profile: vllm) 가 떠 있어야 동작.
    vllm_base_url: str = Field(default="http://vllm:8000/v1")
    vllm_base_url_local: str = Field(default="http://localhost:8001/v1")
    vllm_model: str = Field(default="Qwen/Qwen2.5-7B-Instruct")
    # vLLM 컨테이너 구동 파라미터 — env 는 시드/fallback, DB(app_settings) override 가 우선.
    # 변경 시 vLLM 재구동 필요 (scripts/vllm_restart.sh 가 DB 값 읽어 주입).
    vllm_dtype: str = Field(default="auto")
    vllm_gpu_mem_util: float = Field(default=0.85)
    vllm_max_model_len: int = Field(default=8192)
    vllm_max_batched_tokens: int = Field(default=16384)
    vllm_kv_cache_dtype: str = Field(default="auto")

    # Hugging Face token — gated 모델 (Llama 3 등) 받을 때 필요. 공개 모델 (Qwen 등) 만이면 빈.
    hf_token: str = Field(default="")

    # ─── OpenClaw (LinkMind는 client로서 호출만; 통합 시점에 사용) ──
    openclaw_gateway_url: str = Field(default="http://localhost:7890")
    openclaw_api_key: str = Field(default="")

    # ─── Telegram inbox watcher (ai_agents/telegram_inbox_watcher.py) ──
    # CLAUDE.md §3 정신상 LinkMind backend 안에 봇 코드는 두지 않고, scripts/ 의
    # 별 process daemon 이 채널 메시지를 받아 LinkMind HTTP API 를 호출. 아래는
    # 그 daemon 이 읽을 시크릿/위치만. 미설정이면 watcher 가 자체 skip.
    # api_id 는 정수지만 env 가 빈 문자열일 수 있으므로 str 로 받고 사용 시 int().
    telegram_api_id: str = Field(default="")       # my.telegram.org 에서 발급 (숫자)
    telegram_api_hash: str = Field(default="")     # my.telegram.org 에서 발급 (32자)
    telegram_session_path: str = Field(default="volumes/telegram/inbox.session")
    # multi-channel 운영 (2026-05-23) — invite link / 채널명 / 메타를 yaml 로 단일 관리.
    # watcher 가 yaml 순서대로 한 채널씩 [resolve → backfill → 다음] 순차 처리
    # (2026-05-25 리팩토링 — GPU VRAM 한계로 어차피 병렬 ingest 불가).
    # 비밀이 아니므로 git commit 가능. env 에는 경로만 (12-factor).
    # yaml 미존재 시 watcher 가 channel 0 으로 listen 불가 종료.
    telegram_channels_config: str = Field(default="config/telegram_channels.yaml")

    # ─── Storage ──────────────────────────────────────────────────
    storage_backend: Literal["local", "minio"] = Field(default="local")
    storage_local_path: str = Field(default="./volumes/archive")

    minio_endpoint: str = Field(default="http://minio:9000")
    minio_access_key: str = Field(default="")
    minio_secret_key: str = Field(default="")
    minio_bucket: str = Field(default="linkmind")

    # ─── Slack / Telegram (옵션) ─────────────────────────────────
    slack_export_path: str = Field(default="./archive/slack_export")
    slack_bot_token: str = Field(default="")
    slack_signing_secret: str = Field(default="")

    telegram_ingest_bot_token: str = Field(default="")
    telegram_ingest_chat_id: str = Field(default="")
    telegram_query_bot_token: str = Field(default="")
    telegram_query_chat_id: str = Field(default="")

    # ─── Versioning (학습 데이터 추적용) ──────────────────────────
    analysis_prompt_version: str = Field(default="v1")

    # ─── 편의 프로퍼티 ────────────────────────────────────────────
    @property
    def effective_database_url(self) -> str:
        """호스트에서 실행 중이면 DATABASE_URL_LOCAL을 쓰는 게 안전."""
        # 환경변수 IN_DOCKER가 명시되면 컨테이너 URL, 아니면 LOCAL.
        import os
        if os.getenv("IN_DOCKER") == "1":
            return self.database_url
        return self.database_url_local

    @property
    def effective_qdrant_url(self) -> str:
        import os
        if os.getenv("IN_DOCKER") == "1":
            return self.qdrant_url
        return self.qdrant_url_local

    @property
    def effective_ollama_base_url(self) -> str:
        """LinkMind backend 가 호스트에서 직접 돌면 localhost:11434, docker compose
        안의 backend 컨테이너에서 돌면 docker 서비스명(ollama:11434)."""
        import os
        if os.getenv("IN_DOCKER") == "1":
            return self.ollama_base_url
        return self.ollama_base_url_local

    @property
    def effective_vllm_base_url(self) -> str:
        """ollama 와 동일 패턴 — backend 가 docker 안 (vllm:8000/v1) 또는
        호스트 (localhost:8001/v1)."""
        import os
        if os.getenv("IN_DOCKER") == "1":
            return self.vllm_base_url
        return self.vllm_base_url_local

    @property
    def effective_vllm_embed_base_url(self) -> str:
        """vllm-embed 서버 — backend / watcher / CLI 모두 호스트에서 도니까 보통
        local (8002). 향후 backend 컨테이너화 시 IN_DOCKER=1 분기로 vllm-embed:8000 사용."""
        import os
        if os.getenv("IN_DOCKER") == "1":
            return self.vllm_embed_base_url
        return self.vllm_embed_base_url_local

    @property
    def storage_local_abs_path(self) -> Path:
        """STORAGE_LOCAL_PATH가 상대경로면 PROJECT_ROOT 기준으로 절대화."""
        p = Path(self.storage_local_path)
        return p if p.is_absolute() else (PROJECT_ROOT / p).resolve()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """프로세스 전역에서 하나만 유지되는 settings 싱글톤.

    settings 로드 시 부수효과로 HF_HUB_OFFLINE / TRANSFORMERS_OFFLINE 을 process
    env 에 setdefault export. 이렇게 안 하면 env/dev.env 의 HF_HUB_OFFLINE 값은
    pydantic Settings 객체에만 들어오고 sentence-transformers/huggingface_hub 가
    직접 읽는 환경변수로는 전달 안 됨 → 매 startup 마다 Hub 호출 + 토큰 경고.
    """
    s = Settings()  # type: ignore[call-arg]
    if s.hf_hub_offline:
        import os
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    return s
