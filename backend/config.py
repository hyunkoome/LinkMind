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
    embedding_backend: Literal["local", "tei", "ollama"] = Field(default="local")
    embedding_model: str = Field(default="BAAI/bge-m3")
    embedding_dim: int = Field(default=1024)
    tei_url: str = Field(default="http://tei:80")
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
    default_llm_provider: Literal["openai", "claude", "ollama"] = Field(default="ollama")

    openai_api_key: str = Field(default="")
    openai_model: str = Field(default="gpt-4o-mini")

    anthropic_api_key: str = Field(default="")
    anthropic_model: str = Field(default="claude-haiku-4-5-20251001")

    openrouter_api_key: str = Field(default="")
    openrouter_model: str = Field(default="")

    ollama_base_url: str = Field(default="http://ollama:11434")
    ollama_base_url_local: str = Field(default="http://localhost:11434")
    ollama_model: str = Field(default="qwen2.5:7b")

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
    telegram_inbox_invite: str = Field(default="") # 채널 invite link 또는 채널명/ID
    # ingest 성공 시 채널의 그 메시지를 자동 삭제 — 처리되지 않은 것만 채널에 남음
    # (inbox 패턴). False 면 메시지 그대로 둠.
    telegram_delete_after_ingest: bool = Field(default=True)

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
