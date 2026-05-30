"""
/settings/* — UI(Streamlit Settings 탭) 에서 호출하는 런타임 설정 API.

읽기:
  GET  /settings/llm            -> 현재 effective + config_defaults + override + 활성 prompt
  GET  /settings/llm/models     -> Ollama 에 설치된 모델 목록 + provider 별 default
  GET  /settings/prompts/{name}/versions  -> 특정 prompt 의 버전 히스토리

쓰기:
  PUT    /settings/llm                       -> provider/model 기본값 override 갱신
  POST   /settings/prompts/{name}            -> 새 prompt 버전 저장 + 활성화
  POST   /settings/prompts/{name}/activate   -> 기존 버전 활성화

prompt name 은 'rag_system' | 'summary_system' 만 허용.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend import runtime_settings
from backend.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter()


# ─── Schemas ──────────────────────────────────────────────────


class LLMSettingsUpdate(BaseModel):
    """비어있는 문자열 또는 null 은 'override 해제 → env 기본값으로 복귀'."""

    default_llm_provider: str | None = Field(default=None, description="openai|claude|ollama|vllm")
    ollama_model: str | None = None
    openai_model: str | None = None
    anthropic_model: str | None = None
    vllm_model: str | None = None
    # vLLM 컨테이너 구동 파라미터 (변경 후 vLLM 재구동 필요 — scripts/vllm_restart.sh).
    vllm_dtype: str | None = None              # auto | float16 | bfloat16
    vllm_gpu_mem_util: str | None = None       # 0.0~1.0 (문자열로 저장)
    vllm_max_model_len: str | None = None      # e.g. 16384
    vllm_max_batched_tokens: str | None = None  # e.g. 16384
    vllm_kv_cache_dtype: str | None = None     # auto | fp8


class PromptSave(BaseModel):
    content: str = Field(..., min_length=1)
    note: str | None = None


class PromptActivate(BaseModel):
    version: str = Field(..., min_length=1)


class KeywordConfigUpdate(BaseModel):
    """키워드 정규화 설정 — 약어(split 예외) + 별칭. 텍스트(한 줄당 항목).

    None = 변경 안 함, 빈 문자열 = 기본값으로 복귀.
    """
    acronyms: str | None = Field(default=None, description="약어 목록 (한 줄당 하나)")
    aliases: str | None = Field(default=None, description="별칭 'from = to' (한 줄당)")


# ─── LLM settings ─────────────────────────────────────────────


@router.get("/llm")
async def get_llm_settings() -> dict[str, Any]:
    return await runtime_settings.snapshot()


@router.put("/llm")
async def update_llm_settings(payload: LLMSettingsUpdate) -> dict[str, Any]:
    # provider 값 검증 (빈/None 은 override 해제로 통과)
    if payload.default_llm_provider and payload.default_llm_provider not in (
        "openai", "claude", "ollama", "vllm",
    ):
        raise HTTPException(400, f"알 수 없는 provider: {payload.default_llm_provider}")
    return await runtime_settings.update_settings(payload.model_dump())


# ─── 키워드 정규화 설정 (약어/별칭) ───────────────────────────


@router.get("/keywords")
async def get_keyword_config() -> dict[str, Any]:
    """현재 적용 중인 약어/별칭 텍스트 + 코드 기본값."""
    return runtime_settings.keyword_config_snapshot()


@router.put("/keywords")
async def update_keyword_config(payload: KeywordConfigUpdate) -> dict[str, Any]:
    """약어/별칭 저장 + 즉시 반영 (신규 ingest 부터 적용). 기존 데이터는 reapply 로."""
    return await runtime_settings.update_keyword_config(
        acronyms=payload.acronyms, aliases=payload.aliases,
    )


@router.post("/keywords/reapply")
async def reapply_keyword_config() -> dict[str, Any]:
    """현재 설정으로 기존 모든 wiki 의 keywords 재정규화. 변경 수 반환."""
    from backend.jobs.normalize_keywords import reapply_all
    stats = await reapply_all()
    return {"ok": True, **stats}


@router.get("/llm/models")
async def list_models() -> dict[str, Any]:
    """Provider 별 사용 가능한 모델 목록.

    - ollama: `/api/tags` 로 실제 설치된 모델 조회
    - openai/claude: 설정된 default 모델만 (사용자가 직접 입력해서 다른 모델도 가능)
    """
    settings = get_settings()
    out: dict[str, Any] = {
        "providers": {
            "openai": {
                "available": bool(settings.openai_api_key),
                "default": runtime_settings.get_effective_openai_model(),
                "models": [runtime_settings.get_effective_openai_model()],
            },
            "claude": {
                "available": bool(settings.anthropic_api_key),
                "default": runtime_settings.get_effective_anthropic_model(),
                "models": [runtime_settings.get_effective_anthropic_model()],
            },
            "ollama": {
                "available": True,
                "default": runtime_settings.get_effective_ollama_model(),
                "models": [],
            },
            "vllm": {
                "available": False,    # vLLM 서버 가동 시 True
                "default": runtime_settings.get_effective_vllm_model(),
                "models": [],
            },
        }
    }
    # Ollama 설치된 모델 — 실패해도 다른 정보는 반환.
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{settings.effective_ollama_base_url}/api/tags")
            r.raise_for_status()
            data = r.json()
            out["providers"]["ollama"]["models"] = [m["name"] for m in data.get("models", [])]
    except Exception as e:  # noqa: BLE001
        logger.warning("Ollama /api/tags 조회 실패: %s", e)
        out["providers"]["ollama"]["error"] = str(e)
    # vLLM 의 OpenAI 호환 /v1/models — 가동 시 현재 서빙 모델 1개 반환.
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{settings.effective_vllm_base_url}/models")
            r.raise_for_status()
            data = r.json()
            models = [m["id"] for m in data.get("data", [])]
            out["providers"]["vllm"]["available"] = True
            out["providers"]["vllm"]["models"] = models
    except Exception as e:  # noqa: BLE001
        logger.info("vLLM /v1/models 조회 실패 (서버 미가동일 수 있음): %s", e)
        out["providers"]["vllm"]["error"] = str(e)
    return out


# ─── Prompts ──────────────────────────────────────────────────


_ALLOWED_PROMPT_NAMES = {"rag_system", "summary_system"}


def _check_name(name: str) -> None:
    if name not in _ALLOWED_PROMPT_NAMES:
        raise HTTPException(
            404,
            f"prompt name '{name}' 은 지원되지 않습니다. 가능: {sorted(_ALLOWED_PROMPT_NAMES)}",
        )


@router.get("/prompts/{name}/versions")
async def list_prompt_versions(name: str) -> dict[str, Any]:
    _check_name(name)
    versions = await runtime_settings.list_versions(name)
    return {"name": name, "versions": versions}


@router.post("/prompts/{name}")
async def save_prompt(name: str, payload: PromptSave) -> dict[str, Any]:
    _check_name(name)
    return await runtime_settings.save_prompt(
        name=name, content=payload.content, note=payload.note
    )


@router.post("/prompts/{name}/activate")
async def activate_prompt(name: str, payload: PromptActivate) -> dict[str, Any]:
    _check_name(name)
    return await runtime_settings.activate_version(name=name, version=payload.version)
