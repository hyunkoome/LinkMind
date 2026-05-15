#!/usr/bin/env bash
# GitHub Actions 가 도는 카테고리만 시뮬레이션 — cpu + embedding + integration.
# local/* (llm/gpu) 는 호출 자체 안 함.
# CI 실패 가능성을 push 전 로컬에서 미리 점검할 때.
set -uo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/_lib.sh"
env_report
run_cpu
run_embedding
run_integration
print_summary
