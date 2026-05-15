#!/usr/bin/env bash
# [LOCAL ONLY] CUDA device 가 필요한 smoke — 로컬 GPU 머신 전용.
# nvidia-smi 가 없으면 자동 skip. CI 환경엔 GPU 없음 → SKIP.
# 가벼운 MiniLM 모델로 CUDA tensor + sentence-transformers GPU 흐름 검증.
set -uo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/_lib.sh"
env_report
run_gpu
print_summary
