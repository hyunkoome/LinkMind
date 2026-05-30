#!/usr/bin/env bash
# scripts/run_description_backfill.sh
# ----------------------------------------------------------------------------
# wiki_pages.description 을 body 의 TL;DR 로 통일 (리스트 카드 미리보기).
#
# 옛 description 은 item summary(ingest 시 생성)를 복사해 채웠는데 Qwen 시절 중국어가
# 섞였다. body 는 이미 Gemma 한국어 TL;DR 을 가지므로 그걸로 통일. LLM 호출 없이
# 파싱만 하므로 수초면 끝난다 (idempotent — 재실행 안전).
#
# 사용:
#   bash scripts/run_description_backfill.sh --dry-run        # 미리보기 (변경 안 함)
#   bash scripts/run_description_backfill.sh                  # 전체 통일
#   bash scripts/run_description_backfill.sh --only-foreign   # 중국어 섞인 description 만
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

exec python -m backend.jobs.backfill_description_from_tldr "$@"
