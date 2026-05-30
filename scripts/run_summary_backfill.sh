#!/usr/bin/env bash
# scripts/run_summary_backfill.sh
# ----------------------------------------------------------------------------
# items.summary 를 LLM(현재 Gemma)으로 (재)생성. /ask RAG + 검색 + writer 입력 자료.
#
# 가장 흔한 용도 — 중국어 섞인 옛 summary(Qwen 시절) 정리:
#   bash scripts/run_summary_backfill.sh --only-foreign   # 중국어/일본어 섞인 것만 (현재 902건)
#
# 그 외:
#   bash scripts/run_summary_backfill.sh                  # summary IS NULL 인 것만
#   bash scripts/run_summary_backfill.sh --force          # 전체 재생성 (과함, 주의)
#   bash scripts/run_summary_backfill.sh <item_id>        # 특정 item 1개
#
# ⚠️ GPU 공유 — vLLM(Gemma)/vllm-embed 가 별 컨테이너라 backend 떠 있어도 되지만,
#    백필 중에는 /ask·재합성이 느려질 수 있다. idempotent — 끊겨도 재실행 시 남은 것만.
#    신규 ingest 는 이미 Gemma 라 한국어 — 이 백필은 기존 데이터 정리용.
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

exec python -m backend.jobs.backfill_summary "$@"
