#!/usr/bin/env bash
# scripts/vllm_restart.sh
# ----------------------------------------------------------------------------
# DB(app_settings) 의 vLLM 설정으로 vLLM 컨테이너 재구동.
#
# Settings UI 에서 vLLM 파라미터(model/dtype/gpu_mem/max_len 등)를 바꾼 뒤 실행하면,
# DB 값을 읽어 docker compose 환경변수로 주입하고 컨테이너를 재생성한다.
# (vLLM 은 구동 시점에만 인자를 읽으므로 재구동이 필요.)
#
# 사용:
#   bash scripts/vllm_restart.sh --dry-run    # 적용될 값만 확인
#   bash scripts/vllm_restart.sh              # 실제 재구동
# ----------------------------------------------------------------------------

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

if [ ! -f ".venv/bin/activate" ]; then
    echo "❌  .venv 없음 — 먼저 'bash scripts/step1_install_base_env.sh'" >&2
    exit 1
fi
source .venv/bin/activate

exec python -m backend.jobs.vllm_restart "$@"
