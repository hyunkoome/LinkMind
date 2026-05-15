#!/usr/bin/env bash
# 로컬 풀 스위트 — ci/* 3개 + local/* 2개 = 5 카테고리 모두 순차.
# 환경 미충족 (backend / Ollama / GPU 없음) 카테고리는 SKIP — pass/fail 만 집계.
set -uo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/_lib.sh"
env_report
run_cpu
run_embedding
run_integration
run_llm
run_gpu
print_summary
