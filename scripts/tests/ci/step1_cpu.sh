#!/usr/bin/env bash
# [CI ready] CPU + mock + fixture 만 — GitHub Actions fast job 과 동일. ≈4s.
# pytest marker 가 없는 pure unit / mock 테스트 (83건 기준).
set -uo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/_lib.sh"
env_report
run_cpu
print_summary
