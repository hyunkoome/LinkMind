#!/usr/bin/env bash
# [CI ready] sentence-transformers MiniLM (~80MB) CPU smoke. ≈15-30s.
# 의존성: sentence-transformers + torch. requirements-test.txt 에 포함되면 CI 도 가능.
# 첫 실행 시 모델 다운로드 (~80MB).
set -uo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/_lib.sh"
env_report
run_embedding
print_summary
