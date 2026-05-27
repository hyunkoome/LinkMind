#!/usr/bin/env bash
# scripts/run_wiki_merge.sh
# ----------------------------------------------------------------------------
# 같은 source title 의 wiki 들을 1 wiki 로 자동 merge (D10.5, 2026-05-27).
#
# 배경: D11 classifier ca431aa 의 self_wiki 무조건 INSERT + 같은 콘텐츠 multi
# modality ingest 로 wiki 가 분열. 약 5,700 group 의 wiki 가 중복.
#
# 사용:
#   bash scripts/run_wiki_merge.sh --dry-run                # 진단 (변경 X)
#   bash scripts/run_wiki_merge.sh                          # 백그라운드 시작 (nohup)
#   bash scripts/run_wiki_merge.sh --status                 # 진행 상태 + log 최근 5줄
#   bash scripts/run_wiki_merge.sh --tail                   # log 실시간
#   bash scripts/run_wiki_merge.sh --stop                   # graceful (현재 group 끝나고)
#   bash scripts/run_wiki_merge.sh --stop-force             # 즉시 SIGKILL
#   bash scripts/run_wiki_merge.sh --foreground             # tqdm 직접
#   bash scripts/run_wiki_merge.sh --limit 100              # 100 group 만
#
# 안전성:
#   - group 마다 별 transaction (한 group commit 또는 전체 rollback)
#   - 중간 종료 시: 그때까지 commit 된 group 은 안전, 미처리 group 은 그대로 남음
#   - 다시 실행하면 미처리 group 부터 자동 재개 (이미 merge 된 그룹은 카운트=1 이라 자동 skip)
# ----------------------------------------------------------------------------

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

PID_FILE="/tmp/linkmind-wiki-merge.pid"
LOG_FILE="/tmp/wiki_merge.log"

cd "$PROJECT_ROOT"

if [ ! -f ".venv/bin/activate" ]; then
    echo "❌  .venv 없음 — 먼저 'bash scripts/step1_install_base_env.sh'" >&2
    exit 1
fi
# shellcheck disable=SC1091
source .venv/bin/activate

# python 모듈 호출 형식만 매칭 — bash command line 의 module string false positive 회피.
PROCESS_NAME_PATTERN='python.*-m[[:space:]]+backend\.jobs\.merge_duplicate_wikis'

find_running_pids() {
    pgrep -f "$PROCESS_NAME_PATTERN" 2>/dev/null | tr '\n' ' '
}

is_running() {
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE" 2>/dev/null)" 2>/dev/null; then
        return 0
    fi
    local pids; pids="$(find_running_pids)"
    [ -n "$pids" ]
}

show_status() {
    echo "📊 wiki_pages 현재 중복 group:"
    docker exec linkmind-postgres psql -U linkmind -d linkmind -c "
        SELECT COUNT(*) AS duplicate_groups, SUM(cnt - 1) AS extras_to_remove
        FROM (
            SELECT LOWER(TRIM(title)) AS norm_title, COUNT(*) AS cnt
            FROM wiki_pages
            WHERE LENGTH(TRIM(title)) >= 20
              AND TRIM(title) NOT IN ('Social Media Title Tag','Abstract','[no-title]','paper_title','untitled','no title')
              AND TRIM(title) NOT ILIKE '%.com'
              AND TRIM(title) NOT ILIKE '%.co.kr'
              AND TRIM(title) NOT ILIKE '%.org'
              AND TRIM(title) NOT ILIKE '%.net'
              AND TRIM(title) NOT ILIKE '%.io'
              AND TRIM(title) NOT ILIKE 'www.%'
            GROUP BY LOWER(TRIM(title))
            HAVING COUNT(*) >= 2
        ) g;
    " 2>/dev/null || echo "  (Postgres 접근 실패)"
    echo ""
    if is_running; then
        local pids; pids="$(find_running_pids)"
        local fpid="(none)"
        [ -f "$PID_FILE" ] && fpid="$(cat "$PID_FILE" 2>/dev/null)"
        echo "🟢 merge job 실행 중"
        echo "   PID file: $fpid"
        echo "   이름 매칭 ($PROCESS_NAME_PATTERN): $pids"
        if [ -f "$LOG_FILE" ]; then
            echo ""
            echo "📜 log 최근 5줄 ($LOG_FILE):"
            tail -n 5 "$LOG_FILE" 2>/dev/null | sed 's/^/  /'
        fi
    else
        echo "⚫ merge job 안 돌고 있음"
        if [ -f "$PID_FILE" ]; then
            echo "  (옛 PID file 존재 — 이전 실행 비정상 종료. 다음 시작 시 자동 cleanup)"
        fi
    fi
}

stop_graceful() {
    if ! is_running; then
        echo "⚫ 실행 중 아님"
        rm -f "$PID_FILE"
        return 0
    fi
    local pids; pids="$(find_running_pids)"
    if [ -f "$PID_FILE" ]; then
        local fpid; fpid="$(cat "$PID_FILE" 2>/dev/null)"
        if [ -n "$fpid" ] && kill -0 "$fpid" 2>/dev/null; then
            case " $pids " in
                *" $fpid "*) ;;
                *) pids="$pids $fpid" ;;
            esac
        fi
    fi
    echo "🛑 graceful 종료 신호 (SIGINT) — PIDs: $pids"
    echo "    현재 group 끝나면 종료. 즉시 원하면: --stop-force"
    for p in $pids; do
        kill -INT "$p" 2>/dev/null || true
    done
    local waited=0
    while [ -n "$(find_running_pids)" ]; do
        if [ $waited -ge 60 ]; then
            echo "⚠️  60초 후에도 안 죽음 — 강제 종료"
            pkill -KILL -f "$PROCESS_NAME_PATTERN" 2>/dev/null || true
            break
        fi
        sleep 1
        waited=$((waited + 1))
        if [ $((waited % 10)) -eq 0 ]; then
            echo "  ... ${waited}s 대기 중"
        fi
    done
    rm -f "$PID_FILE"
    echo "✅ 종료 완료"
}

stop_force() {
    if ! is_running; then
        echo "⚫ 실행 중 아님"
        rm -f "$PID_FILE"
        return 0
    fi
    if [ -f "$PID_FILE" ]; then
        local pid; pid="$(cat "$PID_FILE" 2>/dev/null)"
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            echo "⚡ 강제 종료 — PID=$pid"
            kill -KILL "$pid" 2>/dev/null || true
            pkill -KILL -P "$pid" 2>/dev/null || true
        fi
    fi
    local name_pids; name_pids="$(find_running_pids)"
    if [ -n "$name_pids" ]; then
        echo "⚡ 강제 종료 — 이름 매칭: PID=$name_pids"
        pkill -KILL -f "$PROCESS_NAME_PATTERN" 2>/dev/null || true
    fi
    sleep 1
    if [ -n "$(find_running_pids)" ]; then
        pkill -KILL -f "$PROCESS_NAME_PATTERN" 2>/dev/null || true
    fi
    rm -f "$PID_FILE"
    echo "✅ 강제 종료 완료"
}

start_job() {
    # 이미 실행 중이면 즉시 SIGKILL 후 재시작 (run_wiki_backfill 패턴 동일).
    if is_running; then
        local pid_info=""
        if [ -f "$PID_FILE" ]; then
            pid_info="PID file=$(cat "$PID_FILE" 2>/dev/null)"
        fi
        local name_pids; name_pids="$(find_running_pids)"
        if [ -n "$name_pids" ]; then
            pid_info="$pid_info, 이름 매칭=$name_pids"
        fi
        echo "🔄 이미 merge job 실행 중 ($pid_info) — 즉시 종료 후 재시작"
        stop_force
    fi
    [ -f "$PID_FILE" ] && rm -f "$PID_FILE"

    local mode="$1"; shift
    local extra_args=("$@")

    if [ "$mode" = "foreground" ]; then
        echo "▶️  merge_duplicate_wikis 실행 (foreground, tqdm 직접 표시)"
        echo "    args: ${extra_args[*]}"
        echo ""
        exec python -m backend.jobs.merge_duplicate_wikis "${extra_args[@]}"
    fi

    echo "▶️  merge_duplicate_wikis 백그라운드 시작 (nohup)"
    echo "    log: $LOG_FILE"
    echo "    args: ${extra_args[*]}"
    nohup python -m backend.jobs.merge_duplicate_wikis "${extra_args[@]}" \
        > "$LOG_FILE" 2>&1 &
    local pid=$!
    echo "$pid" > "$PID_FILE"
    sleep 2
    if kill -0 "$pid" 2>/dev/null; then
        echo "✅ 시작됨 (PID=$pid)"
        echo ""
        echo "─ 모니터/중단 명령 ─────────────────────────────────"
        echo "📜 진행률 실시간:  bash scripts/run_wiki_merge.sh --tail"
        echo "📊 요약 상태:      bash scripts/run_wiki_merge.sh --status"
        echo "🛑 graceful 중단:  bash scripts/run_wiki_merge.sh --stop"
        echo "⚡ 강제 중단:      bash scripts/run_wiki_merge.sh --stop-force"
        echo "🔄 재시작:         bash scripts/run_wiki_merge.sh   (옛 거 자동 SIGKILL)"
        echo "────────────────────────────────────────────────────"
        echo ""
        echo "⏱  예상 소요: group 당 평균 ~0.5초 (대부분 vLLM 없이 — body 비어있어서)"
        echo "             vLLM merge 호출 필요한 group 만 ~5초/group"
        echo "             5,700 group 이라 전체 ~약 1-3시간 (둘 다 body 있는 비율에 따라)"
    else
        echo "❌  실행 직후 죽음 — log 확인:"
        tail -n 30 "$LOG_FILE" 2>/dev/null
        exit 1
    fi
}

# ─────────────────────────────────────────────────────────────────────
# argument parse
# ─────────────────────────────────────────────────────────────────────

MODE="background"
EXTRA=()

while [ $# -gt 0 ]; do
    case "$1" in
        --status)
            show_status
            exit 0
            ;;
        --tail)
            if [ ! -f "$LOG_FILE" ]; then
                echo "❌  log 파일 없음: $LOG_FILE"
                exit 1
            fi
            echo "📜 tail -f $LOG_FILE  (Ctrl+C 모니터 종료, job 은 계속)"
            exec tail -f "$LOG_FILE"
            ;;
        --stop)
            stop_graceful
            exit 0
            ;;
        --stop-force)
            stop_force
            exit 0
            ;;
        --foreground|-f)
            MODE="foreground"
            shift
            ;;
        --help|-h)
            sed -n '1,25p' "$0" | sed 's/^# \?//'
            exit 0
            ;;
        --dry-run|--limit|--min-len)
            EXTRA+=("$1")
            shift
            if [ $# -gt 0 ] && [[ ! "$1" =~ ^-- ]]; then
                EXTRA+=("$1")
                shift
            fi
            ;;
        *)
            echo "❌  알 수 없는 옵션: $1"
            echo "    --help 으로 사용법 확인"
            exit 1
            ;;
    esac
done

start_job "$MODE" "${EXTRA[@]}"
