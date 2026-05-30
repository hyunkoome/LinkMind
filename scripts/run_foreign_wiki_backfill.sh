#!/usr/bin/env bash
# scripts/run_foreign_wiki_backfill.sh
# ----------------------------------------------------------------------------
# 이미 저장된 wiki_pages.body 중 **중국어/일본어가 섞인** 페이지를 한국어로 일괄 재생성.
#
# 배경: 본문 합성 모델(Qwen2.5-7B)이 중국 모델이라, source 가 중국어/일본어면
#       "중국어 금지" 프롬프트 규칙을 어기고 그 언어로 본문을 써버렸다.
#       writer 에 언어 안전장치(재생성 루프)를 넣은 뒤(2026-05-30), 그 이전에
#       생성된 오염 위키를 이 스크립트로 정리. 재생성하면 writer 가 한국어로 다시 씀.
#
# ⚠️ GPU 공유 — 텔레그램 ingest / 다른 backfill 이 도는 동안엔 vLLM 부하가 겹친다.
#    **ingest 가 끝난 뒤** 실행 권장. 부하 낮추려면 --concurrency 1~2.
#
# 사용:
#   bash scripts/run_foreign_wiki_backfill.sh --dry-run        # 대상 수 + 오염 sample 만 (먼저 확인!)
#   bash scripts/run_foreign_wiki_backfill.sh                  # 전체 재생성 (concurrency 4)
#   bash scripts/run_foreign_wiki_backfill.sh --limit 50       # 50 page 만 (시험)
#   bash scripts/run_foreign_wiki_backfill.sh --concurrency 2  # vLLM 부하 낮게
#   bash scripts/run_foreign_wiki_backfill.sh --slug 'github__%'  # slug 패턴만
#
# idempotent — 재실행 안전. 한국어로 다시 써지면 다음 실행 때 후보에서 빠진다.
# ----------------------------------------------------------------------------

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# venv 활성화
if [ ! -f ".venv/bin/activate" ]; then
    echo "❌  .venv 없음 — 먼저 'bash scripts/step1_install_base_env.sh'" >&2
    exit 1
fi
source .venv/bin/activate

echo "🔄  중국어/일본어 섞인 wiki body 재생성 — backend.jobs.regenerate_foreign_wikis"
echo "    (먼저 --dry-run 으로 대상 수를 확인하는 것을 권장)"
echo

# 인자를 그대로 python job 에 전달 (--dry-run / --limit / --concurrency / --slug)
exec python -m backend.jobs.regenerate_foreign_wikis "$@"
