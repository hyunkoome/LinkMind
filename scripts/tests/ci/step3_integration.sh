#!/usr/bin/env bash
# [CI ready] backend FastAPI 가 떠 있어야 도는 e2e (응답 contract 검증).
# 로컬: `bash scripts/step5_run_dev.sh --backend-only` 띄우고 실행.
# CI: service 컨테이너 미설정이면 fixture 가 pytest.skip — 안전 fallback.
set -uo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/_lib.sh"
env_report
run_integration
print_summary
