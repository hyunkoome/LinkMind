"""
backend/jobs/vllm_restart.py
────────────────────────────
DB(app_settings) 의 vLLM effective 설정을 읽어 vLLM 컨테이너를 재구동.

배경: vLLM 컨테이너는 구동 시점에 command 인자(--model/--dtype/--gpu-memory-utilization
등)를 읽으므로, Settings UI 에서 DB 값을 바꿔도 컨테이너를 재구동해야 적용된다.
vLLM 은 LinkMind DB 를 모르므로, 이 스크립트가 DB 값을 읽어 docker compose 의
환경변수(VLLM_*)로 주입한 뒤 컨테이너를 재생성한다.

우선순위: DB(app_settings) override > env/dev.env > config.py default.
(get_effective_vllm_* 가 그 순서를 보장. 여기선 그 결과를 subprocess env 로 넘겨
docker compose 의 ${VLLM_*} 치환에 쓴다 — shell env 가 --env-file 보다 우선.)

실행:
  python -m backend.jobs.vllm_restart --dry-run    # 적용될 값만 출력 (재구동 X)
  python -m backend.jobs.vllm_restart              # DB 값으로 vLLM 재구동
  bash scripts/vllm_restart.sh [--dry-run]         # shell 래퍼 (venv + cwd)
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess

from backend import runtime_settings as rs
from backend.db.connection import close_engine

_COMPOSE_CMD = [
    "docker", "compose",
    "--env-file", "env/dev.env",
    "-f", "compose/docker-compose.dev.yml",
    "--profile", "vllm",
    "up", "-d", "--force-recreate", "vllm",
]


async def _read_effective_env() -> dict[str, str]:
    """DB 캐시 적재 후 vLLM effective 설정을 compose env 형태로 반환."""
    await rs.seed_and_load()
    env = {
        "VLLM_MODEL": rs.get_effective_vllm_model(),
        "VLLM_DTYPE": rs.get_effective_vllm_dtype(),
        "VLLM_GPU_MEM_UTIL": str(rs.get_effective_vllm_gpu_mem_util()),
        "VLLM_MAX_MODEL_LEN": str(rs.get_effective_vllm_max_model_len()),
        "VLLM_MAX_BATCHED_TOKENS": str(rs.get_effective_vllm_max_batched_tokens()),
        "VLLM_KV_CACHE_DTYPE": rs.get_effective_vllm_kv_cache_dtype(),
    }
    await close_engine()
    return env


def main() -> None:
    ap = argparse.ArgumentParser(description="DB 의 vLLM 설정으로 컨테이너 재구동")
    ap.add_argument("--dry-run", action="store_true", help="적용될 값만 출력, 재구동 안 함")
    args = ap.parse_args()

    vllm_env = asyncio.run(_read_effective_env())

    print("📦 DB(app_settings) 기준 vLLM 구동 설정:")
    for k, v in vllm_env.items():
        print(f"    {k}={v}")

    if args.dry_run:
        print("\n--- DRY RUN: 재구동 안 함 ---")
        return

    print("\n🔄 docker compose up -d --force-recreate vllm ...")
    merged = {**os.environ, **vllm_env}   # shell env 가 --env-file 보다 우선 → DB 값 적용
    subprocess.run(_COMPOSE_CMD, env=merged, check=True)
    print("✅ vLLM 재구동 시작 — 모델 로딩까지 수십초. 진행: docker logs -f linkmind-vllm")


if __name__ == "__main__":
    main()
