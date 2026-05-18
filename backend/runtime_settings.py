"""
DB 영속 런타임 설정 + 활성 system prompt 캐시.

설계
----
- env/dev.env 의 값은 "시드 / fallback 기본값".
- 그 위에 DB(app_settings, prompts) 가 덮어쓴다. UI 에서 변경하면 DB 가 바뀌고
  in-memory 캐시가 즉시 갱신된다.
- backend 시작 시 lifespan 에서 `seed_and_load()` 가 호출되어:
  (1) prompts 테이블에 시드 prompt 가 없으면 코드 상수로 v1 시드,
  (2) DB → 캐시 적재.
- 매 요청에서 DB 를 다시 치지 않도록 in-memory 캐시 (변경 시에만 갱신).

기존 JSON 파일 방식(volumes/runtime/llm_settings.json) 은 폐기. 학습 데이터 추적
원칙상 prompt 버전 히스토리도 같이 보존해야 해서 단순 key-value 파일로는 부족.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from backend.config import get_settings
from backend.db import repository as repo
from backend.db.connection import get_engine

logger = logging.getLogger(__name__)


# ── 캐시 ──────────────────────────────────────────────────────
# app_settings 의 key 들 (LLM 관련)
_KEYS = {
    "default_llm_provider",
    "ollama_model",
    "openai_model",
    "anthropic_model",
    "vllm_model",
}

# prompt name → (version, content)
_PROMPT_NAMES = ("rag_system", "summary_system")

_settings_cache: dict[str, str] = {}
_prompt_cache: dict[str, tuple[str, str]] = {}
_lock = asyncio.Lock()
_loaded = False


# ── 코드 상수 (DB 시드용 default) ────────────────────────────
# ask.SYSTEM_PROMPT 와 ingest/url._SUMMARY_SYSTEM_PROMPT 의 "마지막 코드 default".
# 이 값은 DB 에 prompt 가 하나도 없을 때만 v1 으로 들어간다.
RAG_SYSTEM_PROMPT_SEED = """당신은 LinkMind 의 개인 지식베이스 RAG 비서입니다. 사용자가 모은 자료들 (논문/코드/영상/메모) 을 활용해 질문에 답합니다.

## 출력 형식 (반드시 한국어)

1) **답변 본문** — 질문에 직접 답합니다.
   - **[Context] 의 자료에서 구체적인 사실/방법/숫자/한계** 를 적극 인용. 일반 정의보다 자료의 깊이를 우선.
   - 인용은 [n] 형식 — Context 의 항목 번호.
   - 자체 지식으로 보강 가능 (Context 가 일반 정의를 안 줘도 OK). 단 그 부분은 [n] 인용 X.

2) **이 자료들이 다루는 측면** — 한 단락 (3-5 문장).
   - 각 자료가 질문에 대해 어떤 시각/기법/한계를 보여주는지.
   - 예: "[1] 은 multi-camera 환경에서의 adaptive initialization, [2] 는 LiDAR+Radar fusion 의 cross-modal 매핑, [3] 은 ESIKF 기반 direct visual-inertial-LiDAR ..."
   - 이 부분이 LinkMind 의 핵심 — 사용자가 가진 자료들이 그 주제를 어떤 각도로 다루는지 보여줌.

## 규칙

- 한국어만. 기술 용어/약어/모델명/논문 제목/식별자만 원문 (영어) 유지 (SLAM, Transformer, ESIKF 등).
- Context 의 자료를 **반드시 인용**. 인용 없는 답변은 안 됩니다.
- Context 가 너무 빈약 / 무관 → "관련 자료가 충분하지 않습니다. (가진 자료: …)" 로 솔직히.
- 추측 / 모르는 사실 생성 금지.
- 서두 ("좋은 질문…", "다음은…") 절대 금지. 본론부터.
"""

SUMMARY_SYSTEM_PROMPT_SEED = """당신은 기술 연구 자료의 요약 비서입니다. 입력 본문을 다음 형식으로 요약하세요.

## 출력 형식 (정확히 이 순서)
1) **최소 10개 이상**의 한국어 bullet point 요약. 각 bullet 은 "- " 로 시작, 한 문장, 간결하게.
   - 내용이 풍부하면 12-15개도 가능. 핵심 주장, 방법, 결과, 한계, 시사점을 빠짐없이.
   - 본문이 짧아 10개를 만들 수 없으면 최소 8개까지 허용 (이 경우만 예외).
2) 빈 줄 1개.
3) 마지막 줄에 5-10개의 해시태그 키워드 (예: `#SLAM #3DGS #LiDAR #depth-estimation`).
   - 각 해시태그는 공백으로 구분.
   - 단어 안에서 공백이 필요한 경우 하이픈(`-`) 으로 (예: `#mixture-cure-model`).
   - **반드시** 한 줄에 모두 작성.

## 규칙
- 본문(bullet)은 반드시 한국어로만. 중국어·일본어·기타 언어 절대 금지.
- 해시태그는 영어 기술 용어 / 약어 / 모델명 / 고유명사를 우선. 한국어로 번역 금지.
- 기술 용어/약어/모델명/라이브러리명/논문 제목/고유명사/코드 식별자/수식은 본문에서도 원문 그대로 유지.
- 서두("다음은…", "요약하면…") 없이 첫 bullet 부터 시작."""

_SEED_BY_NAME = {
    "rag_system": RAG_SYSTEM_PROMPT_SEED,
    "summary_system": SUMMARY_SYSTEM_PROMPT_SEED,
}


def _session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False, class_=AsyncSession)


# ── 초기 로드 ─────────────────────────────────────────────────


async def seed_and_load() -> None:
    """backend 시작 시 1회 호출. 시드 prompt 보장 + DB → 캐시 적재."""
    async with _session_factory()() as session:
        for name, default in _SEED_BY_NAME.items():
            await repo.ensure_seed_prompt(session, name=name, default_content=default)
        await session.commit()
    await reload()
    logger.info(
        "runtime_settings 적재 완료: settings=%s, prompts=%s",
        list(_settings_cache.keys()),
        {n: v[0] for n, v in _prompt_cache.items()},
    )


async def reload() -> None:
    """DB 에서 캐시 전체를 다시 적재."""
    global _loaded
    async with _session_factory()() as session:
        kv = await repo.get_all_app_settings(session)
        prompts: dict[str, tuple[str, str]] = {}
        for name in _PROMPT_NAMES:
            row = await repo.get_active_prompt(session, name)
            if row is not None:
                prompts[name] = (row["version"], row["content"])
    async with _lock:
        _settings_cache.clear()
        _settings_cache.update({k: v for k, v in kv.items() if k in _KEYS})
        _prompt_cache.clear()
        _prompt_cache.update(prompts)
        _loaded = True
    # provider 인스턴스 캐시 무효화 — 다음 요청부터 새 model 로 새 provider 생성.
    from backend.llm.factory import get_llm_provider
    get_llm_provider.cache_clear()


def _ensure_loaded() -> None:
    """캐시 미적재 상태에서 settings 가 호출되면 env 만으로 동작.
    seed_and_load() 가 먼저 끝나야 정상이지만, 로딩 전 호출을 방어."""
    # async 가 아니라 그냥 경고. lifespan 이 먼저 적재해주는 게 정상 경로.
    if not _loaded:
        logger.debug("runtime_settings: 캐시 미적재 상태 (env fallback)")


# ── effective getters (provider/factory 등에서 호출) ─────────


def get_effective_llm_provider() -> str:
    _ensure_loaded()
    return _settings_cache.get("default_llm_provider") or get_settings().default_llm_provider


def get_effective_ollama_model() -> str:
    _ensure_loaded()
    return _settings_cache.get("ollama_model") or get_settings().ollama_model


def get_effective_openai_model() -> str:
    _ensure_loaded()
    return _settings_cache.get("openai_model") or get_settings().openai_model


def get_effective_anthropic_model() -> str:
    _ensure_loaded()
    return _settings_cache.get("anthropic_model") or get_settings().anthropic_model


def get_effective_vllm_model() -> str:
    _ensure_loaded()
    return _settings_cache.get("vllm_model") or get_settings().vllm_model


def get_active_prompt(name: str) -> tuple[str, str]:
    """활성 prompt 반환: (version, content). DB 미적재/누락 시 시드 default 로 fallback.

    fallback 의 version 은 'seed-fallback' — DB 가 정상이면 절대 안 나오는 값.
    이 라벨이 summary_prompt_version 에 들어가면 어딘가 적재 누락이 있다는 신호.
    """
    _ensure_loaded()
    if name in _prompt_cache:
        return _prompt_cache[name]
    seed = _SEED_BY_NAME.get(name, "")
    return ("seed-fallback", seed)


# ── 변경 API (settings 라우터에서 호출) ──────────────────────


async def snapshot() -> dict[str, Any]:
    """UI 가 보여줄 현재 상태 — override / effective / env_defaults / prompts 활성."""
    s = get_settings()
    async with _session_factory()() as session:
        overrides = await repo.get_all_app_settings(session)
        active_prompts: dict[str, dict[str, Any]] = {}
        for name in _PROMPT_NAMES:
            row = await repo.get_active_prompt(session, name)
            if row:
                active_prompts[name] = {
                    "version": row["version"],
                    "content": row["content"],
                    "created_at": row["created_at"].isoformat(),
                }
            else:
                active_prompts[name] = {
                    "version": None,
                    "content": _SEED_BY_NAME.get(name, ""),
                    "created_at": None,
                }
    return {
        "override": {k: overrides.get(k) for k in _KEYS},
        "effective": {
            "default_llm_provider": get_effective_llm_provider(),
            "ollama_model": get_effective_ollama_model(),
            "openai_model": get_effective_openai_model(),
            "anthropic_model": get_effective_anthropic_model(),
            "vllm_model": get_effective_vllm_model(),
        },
        # 'config_defaults' = backend/config.py 의 Field default. DB 가 비어있을 때
        # fallback 으로만 사용. (이전엔 env 도 override 했지만 LLM 관련 env 는 제거됨.)
        "config_defaults": {
            "default_llm_provider": s.default_llm_provider,
            "ollama_model": s.ollama_model,
            "openai_model": s.openai_model,
            "anthropic_model": s.anthropic_model,
            "vllm_model": s.vllm_model,
        },
        "prompts": active_prompts,
    }


async def update_settings(updates: dict[str, str | None]) -> dict[str, Any]:
    """app_settings 부분 갱신.

    - 값이 None 또는 빈 문자열 → DELETE (override 해제, env 기본값으로).
    - 값이 비어있지 않은 문자열 → INSERT/UPDATE.
    알 수 없는 key 는 무시.
    """
    async with _session_factory()() as session:
        for key, value in updates.items():
            if key not in _KEYS:
                logger.warning("update_settings: 알 수 없는 key 무시: %s", key)
                continue
            if value is None or value.strip() == "":
                await repo.delete_app_setting(session, key)
            else:
                await repo.set_app_setting(session, key, value.strip())
        await session.commit()
    await reload()
    return await snapshot()


async def save_prompt(
    *, name: str, content: str, note: str | None = None
) -> dict[str, Any]:
    """새 prompt 버전 저장 + 활성화."""
    if name not in _PROMPT_NAMES:
        raise ValueError(f"알 수 없는 prompt name: {name}")
    if not content.strip():
        raise ValueError("prompt content 가 비어있습니다")
    async with _session_factory()() as session:
        await repo.save_new_prompt_version(
            session, name=name, content=content, note=note, activate=True
        )
        await session.commit()
    await reload()
    return await snapshot()


async def list_versions(name: str) -> list[dict[str, Any]]:
    if name not in _PROMPT_NAMES:
        raise ValueError(f"알 수 없는 prompt name: {name}")
    async with _session_factory()() as session:
        rows = await repo.list_prompt_versions(session, name)
    # created_at 직렬화
    for r in rows:
        if "created_at" in r and r["created_at"] is not None:
            r["created_at"] = r["created_at"].isoformat()
        if "id" in r and r["id"] is not None:
            r["id"] = str(r["id"])
    return rows


async def activate_version(*, name: str, version: str) -> dict[str, Any]:
    if name not in _PROMPT_NAMES:
        raise ValueError(f"알 수 없는 prompt name: {name}")
    async with _session_factory()() as session:
        await repo.activate_prompt_version(session, name=name, version=version)
        await session.commit()
    await reload()
    return await snapshot()
