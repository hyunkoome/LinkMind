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

    default_llm_provider: str | None = Field(default=None, description="openai|claude|ollama")
    ollama_model: str | None = None
    openai_model: str | None = None
    anthropic_model: str | None = None


class PromptSave(BaseModel):
    content: str = Field(..., min_length=1)
    note: str | None = None


class PromptActivate(BaseModel):
    version: str = Field(..., min_length=1)


# ─── LLM settings ─────────────────────────────────────────────


@router.get("/llm")
async def get_llm_settings() -> dict[str, Any]:
    return await runtime_settings.snapshot()


@router.put("/llm")
async def update_llm_settings(payload: LLMSettingsUpdate) -> dict[str, Any]:
    # provider 값 검증 (빈/None 은 override 해제로 통과)
    if payload.default_llm_provider and payload.default_llm_provider not in (
        "openai", "claude", "ollama",
    ):
        raise HTTPException(400, f"알 수 없는 provider: {payload.default_llm_provider}")
    return await runtime_settings.update_settings(payload.model_dump())


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
