#!/usr/bin/env bash
#
# rebuild_large_paper_wikis.sh — 토큰 초과로 잘리거나 개념형으로 남은 "큰 논문"
# 위키를 논문 구조로 재합성한다 (2026-06-05).
#
# 배경: 과거 writer 는 큰 논문 raw(Docling markdown)를 단일 LLM 호출에 통째로 넣어
#   vLLM context(16384 토큰)를 초과했고, 그 결과 논문 위키가 합성 실패하거나 개념형
#   (body_prompt_version='v1' 등)으로 짧게 잘린 채 남았다. 이후 writer 가 raw 를 섹션별로
#   나눠 압축(map)한 뒤 합성(reduce)하도록 고쳐(map-reduce), 원본이 아무리 커도 안정적으로
#   논문 구조 위키를 만든다. 이 스크립트는 그 새 로직을 옛 위키들에 일괄 적용한다.
#
# 대상: primary source 가 pdf/arxiv 이고 raw_content 가 MIN_RAW 자보다 큰데,
#       body_prompt_version 이 'paper-*' 가 아닌(개념형/잘림) completed 위키.
# 동작: 대상을 body_status='pending' 으로 마킹 → wiki_writer_batch 가 map-reduce 재합성.
#       idempotent — 재실행해도 이미 'paper-*' 가 된 위키는 대상에서 빠진다.
#
# 사용:
#   bash scripts/rebuild_large_paper_wikis.sh --dry-run         # 대상만 출력 (마킹/재합성 X)
#   bash scripts/rebuild_large_paper_wikis.sh                   # 마킹 + 재합성 (foreground, tqdm)
#   bash scripts/rebuild_large_paper_wikis.sh --min-raw 18000   # raw 임계 조정 (기본 22000)
#   bash scripts/rebuild_large_paper_wikis.sh --limit 20        # 큰 것부터 N 개만
#   bash scripts/rebuild_large_paper_wikis.sh --mark-only       # 마킹만 — 재합성은 backend
#                                                                #   daemon 또는 run_wiki_backfill.sh 에 위임
#
# 주의: 초대형 논문(raw 수십만 자)은 map 청크가 많아 한 편에 수 분 걸릴 수 있다. GPU(vLLM)
#       부하가 크므로 한가한 시간에 돌리는 것을 권장한다. 진행 중 backend daemon 이 함께
#       pending 을 집어가도 claim 이 SKIP LOCKED 라 중복 처리되지 않는다.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# ── 옵션 파싱 ─────────────────────────────────────────────────────────
MIN_RAW=22000          # raw_content 글자 수 임계 (이보다 큰 논문만 — 단일 호출 예산 초과 후보)
DRY_RUN=0
MARK_ONLY=0
LIMIT_SQL=""           # "LIMIT n" 또는 빈 문자열
BATCH_ARGS=()          # wiki_writer_batch 로 pass-through (--limit 등)

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run)   DRY_RUN=1 ;;
        --mark-only) MARK_ONLY=1 ;;
        --min-raw)   MIN_RAW="${2:?--min-raw 값 필요}"; shift ;;
        --limit)     LIMIT_SQL="LIMIT ${2:?--limit 값 필요}"; BATCH_ARGS+=(--limit "$2"); shift ;;
        -h|--help)   sed -n '2,40p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *) echo "❌ 알 수 없는 옵션: $1 (--help 참고)" >&2; exit 1 ;;
    esac
    shift
done

PG=(docker exec -i linkmind-postgres psql -U linkmind -d linkmind)

# ── 대상 선정 SQL (미리보기/마킹 공통) ────────────────────────────────
# primary pdf/arxiv + raw>MIN_RAW + completed + paper 아님.
WHERE_TARGETS="
      i.source_type IN ('pdf','arxiv')
      AND length(coalesce(i.raw_content,'')) > ${MIN_RAW}
      AND wp.body_status = 'completed'
      AND (wp.body_prompt_version IS NULL OR wp.body_prompt_version NOT LIKE 'paper-%')
"

echo "📋 대상 미리보기 (primary pdf/arxiv, raw>${MIN_RAW}자, body_prompt_version≠paper-*):"
"${PG[@]}" -c "
    SELECT wp.slug,
           wp.body_prompt_version AS ver,
           length(coalesce(wp.body,'')) AS body_len,
           length(coalesce(i.raw_content,'')) AS raw_len
    FROM wiki_pages wp
    JOIN wiki_page_items wpi ON wpi.wiki_page_id = wp.id AND wpi.role = 'primary'
    JOIN items i ON i.id = wpi.item_id
    WHERE ${WHERE_TARGETS}
    ORDER BY raw_len DESC
    ${LIMIT_SQL};
"

COUNT="$("${PG[@]}" -t -A -c "
    SELECT count(DISTINCT wp.id)
    FROM wiki_pages wp
    JOIN wiki_page_items wpi ON wpi.wiki_page_id = wp.id AND wpi.role = 'primary'
    JOIN items i ON i.id = wpi.item_id
    WHERE ${WHERE_TARGETS};
")"
echo "📊 총 대상: ${COUNT} 개 (raw>${MIN_RAW}자)"

if [ "$DRY_RUN" = "1" ]; then
    echo "🔍 --dry-run — 마킹/재합성 없이 종료."
    exit 0
fi

if [ "${COUNT:-0}" = "0" ]; then
    echo "✅ 재합성할 대상 없음 (이미 모두 paper-* 이거나 조건 불일치)."
    exit 0
fi

# ── pending 마킹 ──────────────────────────────────────────────────────
# id 를 LIMIT 와 함께 고를 때 raw_len DESC 정렬 유지 (큰 것 우선).
echo "🔖 ${COUNT} 개를 body_status='pending' 으로 마킹..."
"${PG[@]}" -c "
    UPDATE wiki_pages SET body_status = 'pending', body_processing_started_at = NULL
    WHERE id IN (
        SELECT wp.id
        FROM wiki_pages wp
        JOIN wiki_page_items wpi ON wpi.wiki_page_id = wp.id AND wpi.role = 'primary'
        JOIN items i ON i.id = wpi.item_id
        WHERE ${WHERE_TARGETS}
        ORDER BY length(coalesce(i.raw_content,'')) DESC
        ${LIMIT_SQL}
    );
"

if [ "$MARK_ONLY" = "1" ]; then
    echo "✅ 마킹 완료. 재합성은 backend daemon 또는 'bash scripts/run_wiki_backfill.sh' 가 처리."
    echo "   진행: docker exec -i linkmind-postgres psql -U linkmind -d linkmind -c \\"
    echo "         \"SELECT body_status, count(*) FROM wiki_pages GROUP BY 1;\""
    exit 0
fi

# ── 재합성 — wiki_writer_batch (pending 을 map-reduce 로 합성) ─────────
if [ ! -f ".venv/bin/activate" ]; then
    echo "❌ .venv 없음 — 먼저 'bash scripts/step1_install_base_env.sh'" >&2
    exit 1
fi
# shellcheck disable=SC1091
source .venv/bin/activate

echo "⚙️  wiki_writer_batch 로 재합성 시작 (map-reduce, concurrency 4)..."
echo "   ※ 초대형 논문은 한 편에 수 분 걸릴 수 있습니다 (Ctrl-C 로 중단 가능 — 남은 건"
echo "      pending 으로 남아 daemon 또는 재실행이 이어서 처리)."
python -m backend.jobs.wiki_writer_batch --status pending "${BATCH_ARGS[@]}"
echo "✅ 재합성 완료."
