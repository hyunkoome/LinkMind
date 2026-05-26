#!/usr/bin/env bash
# scripts/run_wiki_backfill.sh
# ----------------------------------------------------------------------------
# 기존 DB (wiki_pages.body_status='empty' / 'stale') 를 일괄 wiki body 합성.
#
# wave-1g backfill 후 ~23,800 페이지가 body 없음 (사용자 클릭 시점에 합성 X).
# 이 스크립트로 백그라운드 일괄 처리 + 진행률 + ETA + PID lock + log.
#
# 정책 (사용자 2026-05-26):
#   - 옛 23k empty backfill = 사용자 직접 실행 (이 스크립트)
#   - 신규 ingest 자동 wiki = backend lifespan 의 wiki_writer_worker daemon (stale 만)
#   - 두 시스템 disjoint — 옛 페이지는 batch CLI 만 건드림
#
# 사용:
#   bash scripts/run_wiki_backfill.sh                  # 백그라운드 시작 (nohup)
#   bash scripts/run_wiki_backfill.sh --status         # 현재 상태 + log 최근 5줄
#   bash scripts/run_wiki_backfill.sh --tail           # log 실시간 (Ctrl+C 모니터만 종료)
#   bash scripts/run_wiki_backfill.sh --stop           # graceful (현재 page 끝나고 종료)
#   bash scripts/run_wiki_backfill.sh --restart        # stop + start
#   bash scripts/run_wiki_backfill.sh --foreground     # nohup 없이 직접 (tqdm 직접 보기)
#   bash scripts/run_wiki_backfill.sh --dry-run        # 처리 안 함, sample 만
#   bash scripts/run_wiki_backfill.sh --limit 100      # 100 페이지만
#   bash scripts/run_wiki_backfill.sh --slug 'arxiv*'  # slug 패턴 필터
#
# 옵션 조합 예:
#   bash scripts/run_wiki_backfill.sh --slug 'github__%' --limit 500
# ----------------------------------------------------------------------------

set -e

# 프로젝트 root (이 스크립트의 부모)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

PID_FILE="/tmp/linkmind-wiki-writer-batch.pid"   # backend/jobs/wiki_writer_batch.py 와 동일 경로
LOG_FILE="/tmp/wiki_backfill.log"

cd "$PROJECT_ROOT"

# venv 활성화
if [ ! -f ".venv/bin/activate" ]; then
    echo "❌  .venv 없음 — 먼저 'bash scripts/step1_install_base_env.sh'" >&2
    exit 1
fi
# shellcheck disable=SC1091
source .venv/bin/activate

# ─────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────

# pgrep 패턴 — python 모듈 호출 형식만 매칭 (bash command line 의 'wiki_writer_batch'
# 문자열 false positive 회피). 정규식: python.*-m.*backend\.jobs\.wiki_writer_batch
PROCESS_NAME_PATTERN='python.*-m[[:space:]]+backend\.jobs\.wiki_writer_batch'

# 실행 중인 wiki_writer_batch python PID 찾기 (이름 매칭) — PID file 무관.
# 사용자가 외부에서 직접 nohup 한 경우, PID file 수동 삭제된 경우도 잡음.
find_running_pids() {
    pgrep -f "$PROCESS_NAME_PATTERN" 2>/dev/null | tr '\n' ' '
}

is_running() {
    # PID file 또는 이름 매칭 둘 중 하나라도 — 좀비 검출 강화
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE" 2>/dev/null)" 2>/dev/null; then
        return 0
    fi
    local pids; pids="$(find_running_pids)"
    [ -n "$pids" ]
}

show_status() {
    echo "📊 wiki_pages 현재 상태:"
    docker exec linkmind-postgres psql -U linkmind -d linkmind -c "
        SELECT body_status, COUNT(*) FROM wiki_pages GROUP BY body_status ORDER BY body_status;
    " 2>/dev/null || echo "  (Postgres 접근 실패)"
    echo ""
    if is_running; then
        local pids; pids="$(find_running_pids)"
        local fpid="(none)"
        [ -f "$PID_FILE" ] && fpid="$(cat "$PID_FILE" 2>/dev/null)"
        echo "🟢 batch 실행 중"
        echo "   PID file: $fpid"
        echo "   이름 매칭 ($PROCESS_NAME_PATTERN): $pids"
        if [ -f "$LOG_FILE" ]; then
            echo ""
            echo "📜 log 최근 5줄 ($LOG_FILE):"
            tail -n 5 "$LOG_FILE" 2>/dev/null | sed 's/^/  /'
        fi
    else
        echo "⚫ batch 안 돌고 있음"
        if [ -f "$PID_FILE" ]; then
            echo "  (옛 PID file 존재 — 이전 실행 비정상 종료. 다음 시작 시 자동 cleanup)"
        fi
    fi
}

stop_graceful() {
    if ! is_running; then
        echo "⚫ 실행 중 아님 — 종료할 batch 없음"
        rm -f "$PID_FILE"
        return 0
    fi
    # PID file 기반 + 이름 매칭 둘 다 — SIGINT 전송
    local pids; pids="$(find_running_pids)"
    # PID file 의 PID 도 추가 (file 만 있고 이름 매칭 안 잡힌 케이스 보호)
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
    echo "    현재 page 끝나면 종료. 즉시 강제 원하면: --stop-force"
    for p in $pids; do
        kill -INT "$p" 2>/dev/null || true
    done
    # 사라질 때까지 대기 (최대 90초)
    local waited=0
    while [ -n "$(find_running_pids)" ]; do
        if [ $waited -ge 90 ]; then
            echo "⚠️  90초 후에도 안 죽음 — 강제 종료"
            pkill -KILL -f "$PROCESS_NAME_PATTERN" 2>/dev/null || true
            break
        fi
        sleep 1
        waited=$((waited + 1))
        if [ $((waited % 10)) -eq 0 ]; then
            echo "  ... ${waited}s 대기 중 (page 합성 끝나기 기다림)"
        fi
    done
    rm -f "$PID_FILE"
    echo "✅ 종료 완료"
}

stop_force() {
    if ! is_running; then
        echo "⚫ 실행 중 아님 — 종료할 batch 없음"
        rm -f "$PID_FILE"
        return 0
    fi
    # 1) PID file 기반 (있으면)
    if [ -f "$PID_FILE" ]; then
        local pid; pid="$(cat "$PID_FILE" 2>/dev/null)"
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            echo "⚡ 강제 종료 — PID file (PID=$pid)"
            kill -KILL "$pid" 2>/dev/null || true
            pkill -KILL -P "$pid" 2>/dev/null || true
        fi
    fi
    # 2) 이름 매칭 — PID file 누락 / 외부 실행 좀비 잡음
    local name_pids; name_pids="$(find_running_pids)"
    if [ -n "$name_pids" ]; then
        echo "⚡ 강제 종료 — 이름 매칭 ($PROCESS_NAME_PATTERN): PID=$name_pids"
        pkill -KILL -f "$PROCESS_NAME_PATTERN" 2>/dev/null || true
    fi
    sleep 1
    # 확인
    if [ -n "$(find_running_pids)" ]; then
        echo "⚠️  일부 프로세스 아직 살아있음 (1초 후 또 시도):"
        find_running_pids
        sleep 1
        pkill -KILL -f "$PROCESS_NAME_PATTERN" 2>/dev/null || true
    fi
    rm -f "$PID_FILE"
    echo "✅ 강제 종료 완료"
}

start_batch() {
    # default 동작 (2026-05-26 사용자 명시): 이미 실행 중이면 즉시 SIGKILL 후 재시작.
    # 한 줄 명령으로 빠른 재시작. 안전 (vLLM 호출 중 죽여도 DB transaction 은 commit
    # 안 됐으니 rollback → row 상태 변경 X). graceful 원하면 --graceful 옵션.
    if is_running; then
        local pid_info=""
        if [ -f "$PID_FILE" ]; then
            pid_info="PID file=$(cat "$PID_FILE" 2>/dev/null)"
        fi
        local name_pids; name_pids="$(find_running_pids)"
        if [ -n "$name_pids" ]; then
            pid_info="$pid_info, 이름 매칭=$name_pids"
        fi
        echo "🔄 이미 batch 실행 중 ($pid_info) — 즉시 종료 후 재시작"
        stop_force
    fi
    # stale PID file 자동 cleanup (backend/jobs/wiki_writer_batch.py 의 lock 도 같은 경로 검사)
    [ -f "$PID_FILE" ] && rm -f "$PID_FILE"

    local mode="$1"; shift
    local extra_args=("$@")

    if [ "$mode" = "foreground" ]; then
        echo "▶️  wiki_writer_batch 실행 (foreground, tqdm 직접 표시)"
        echo "    args: ${extra_args[*]}"
        echo ""
        exec python -m backend.jobs.wiki_writer_batch "${extra_args[@]}"
    fi

    # background
    echo "▶️  wiki_writer_batch 백그라운드 시작 (nohup)"
    echo "    log: $LOG_FILE"
    echo "    args: ${extra_args[*]}"
    # env LINKMIND_WIKI_BATCH_LOCK_OWNER=shell 으로 python 의 lock 검사 skip
    # (shell 이 PID file 책임 — race 회피)
    LINKMIND_WIKI_BATCH_LOCK_OWNER=shell \
        nohup python -m backend.jobs.wiki_writer_batch "${extra_args[@]}" \
        > "$LOG_FILE" 2>&1 &
    local pid=$!
    echo "$pid" > "$PID_FILE"
    sleep 2
    if kill -0 "$pid" 2>/dev/null; then
        echo "✅ 시작됨 (PID=$pid)"
        echo ""
        echo "─ 모니터/중단 명령 ─────────────────────────────────"
        echo "📜 진행률 실시간:  bash scripts/run_wiki_backfill.sh --tail"
        echo "📊 요약 상태:      bash scripts/run_wiki_backfill.sh --status"
        echo "🛑 graceful 중단:  bash scripts/run_wiki_backfill.sh --stop"
        echo "⚡ 강제 중단:      bash scripts/run_wiki_backfill.sh --stop-force"
        echo "🔄 자동 재시작:    bash scripts/run_wiki_backfill.sh   (옛 거 자동 SIGKILL)"
        echo ""
        echo "─ concurrency 조정 (재시작 시) ──────────────────────"
        echo "🚀 기본 (4):       bash scripts/run_wiki_backfill.sh"
        echo "⚡ 더 빠르게 (8):  bash scripts/run_wiki_backfill.sh --concurrency 8"
        echo "🐢 sequential:     bash scripts/run_wiki_backfill.sh --concurrency 1"
        echo "────────────────────────────────────────────────────"
        echo ""
        echo "⏱  예상 소요: 페이지당 ~3-5초 (concurrency=4, vLLM continuous batching)"
        echo "             23k 페이지 ≈ 약 24 시간 (~1일)"
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
FORCE_KILL=0
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
            echo "📜 tail -f $LOG_FILE  (Ctrl+C 모니터 종료, batch 는 계속)"
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
        --restart)
            # alias — default 동작이 이미 stop+start 라 deprecated. 명시 옵션 유지.
            shift
            ;;
        --force)
            # 자동 재시작 시 graceful (90초 대기) 대신 즉시 SIGKILL.
            FORCE_KILL=1
            shift
            ;;
        --foreground|-f)
            MODE="foreground"
            shift
            ;;
        --help|-h)
            sed -n '1,30p' "$0" | sed 's/^# \?//'
            exit 0
            ;;
        --dry-run|--limit|--slug|--status-filter)
            # backend.jobs.wiki_writer_batch 에 그대로 pass-through.
            # --status-filter 는 batch CLI 의 --status 와 conflict 회피용 alias.
            if [ "$1" = "--status-filter" ]; then
                EXTRA+=("--status")
            else
                EXTRA+=("$1")
            fi
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

start_batch "$MODE" "${EXTRA[@]}"
