#!/usr/bin/env bash
# step5_run_dev.sh — LinkMind 백엔드 (FastAPI) + 프론트엔드 (Streamlit) 동시 기동.
#
# 사용:
#   bash scripts/step5_run_dev.sh             # 백그라운드, 로그 /tmp/linkmind-*.log
#   bash scripts/step5_run_dev.sh --foreground  # 포어그라운드 (Ctrl+C 로 둘 다 종료)
#   bash scripts/step5_run_dev.sh --backend-only
#   bash scripts/step5_run_dev.sh --frontend-only
#   bash scripts/step5_run_dev.sh --stop      # 도는 중인 프로세스 종료
#   bash scripts/step5_run_dev.sh --status    # 상태 + 최근 로그 tail
#
# 인프라 컨테이너 (Postgres/Qdrant/Ollama) 가 떠 있어야 함. 죽었으면
# `bash scripts/step2_2_setup_infra.sh` 로 재기동.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

BACKEND_PORT="${LINKMIND_BACKEND_PORT:-8000}"
FRONTEND_PORT="${LINKMIND_FRONTEND_PORT:-8501}"
HOST="${LINKMIND_HOST:-127.0.0.1}"
BACKEND_LOG="/tmp/linkmind-backend.log"
FRONTEND_LOG="/tmp/linkmind-frontend.log"
PID_DIR="/tmp/linkmind-pids"
mkdir -p "$PID_DIR"
BACKEND_PIDFILE="$PID_DIR/backend.pid"
FRONTEND_PIDFILE="$PID_DIR/frontend.pid"

VENV_PY="$ROOT/.venv/bin/python"
[[ -x "$VENV_PY" ]] || { echo "❌ .venv 없음 — bash scripts/step1_install_base_env.sh 먼저"; exit 1; }

# ── 공통 헬퍼 ────────────────────────────────────────────────

_pid_alive() {
    local f="$1"
    [[ -f "$f" ]] && kill -0 "$(cat "$f")" 2>/dev/null
}

_stop_one() {
    local f="$1"
    local name="$2"
    if _pid_alive "$f"; then
        local pid; pid="$(cat "$f")"
        echo "  · $name (pid=$pid) 종료"
        kill "$pid" 2>/dev/null || true
        # graceful → 2초 후 SIGKILL
        for _ in 1 2; do sleep 1; _pid_alive "$f" || break; done
        if _pid_alive "$f"; then
            kill -9 "$(cat "$f")" 2>/dev/null || true
        fi
    fi
    rm -f "$f"
}

_start_backend() {
    if _pid_alive "$BACKEND_PIDFILE"; then
        echo "ℹ️  backend 이미 가동 중 (pid=$(cat "$BACKEND_PIDFILE"), :$BACKEND_PORT)"
        return
    fi
    echo "▶️  backend (uvicorn) 기동 — :$BACKEND_PORT, log=$BACKEND_LOG"
    nohup "$VENV_PY" -m uvicorn backend.main:app \
        --host "$HOST" --port "$BACKEND_PORT" --reload \
        > "$BACKEND_LOG" 2>&1 &
    echo $! > "$BACKEND_PIDFILE"
    disown 2>/dev/null || true
}

_start_frontend() {
    if _pid_alive "$FRONTEND_PIDFILE"; then
        echo "ℹ️  frontend 이미 가동 중 (pid=$(cat "$FRONTEND_PIDFILE"), :$FRONTEND_PORT)"
        return
    fi
    echo "▶️  frontend (streamlit) 기동 — :$FRONTEND_PORT, log=$FRONTEND_LOG"
    LINKMIND_API_BASE="${LINKMIND_API_BASE:-http://localhost:$BACKEND_PORT}" \
    nohup "$VENV_PY" -m streamlit run frontend/app.py \
        --server.address "$HOST" --server.port "$FRONTEND_PORT" \
        --browser.gatherUsageStats false \
        > "$FRONTEND_LOG" 2>&1 &
    echo $! > "$FRONTEND_PIDFILE"
    disown 2>/dev/null || true
}

_health_check() {
    sleep 2
    if curl -s --max-time 5 "http://localhost:$BACKEND_PORT/health" > /dev/null; then
        echo "✅ backend health OK"
    else
        echo "⚠️  backend health 응답 없음 — log 확인: tail -f $BACKEND_LOG"
    fi
}

# ── 명령 ────────────────────────────────────────────────────

case "${1:-}" in
    --stop)
        echo "🛑 LinkMind 정지"
        _stop_one "$BACKEND_PIDFILE" "backend"
        _stop_one "$FRONTEND_PIDFILE" "frontend"
        echo "완료"
        ;;
    --status)
        echo "== LinkMind 상태 =="
        for pair in "backend $BACKEND_PIDFILE $BACKEND_LOG :$BACKEND_PORT" \
                    "frontend $FRONTEND_PIDFILE $FRONTEND_LOG :$FRONTEND_PORT"; do
            read -r name pidf log port <<< "$pair"
            if _pid_alive "$pidf"; then
                echo "  ✅ $name pid=$(cat "$pidf") port=$port"
            else
                echo "  ⛔ $name 미가동 ($port)"
            fi
        done
        echo
        echo "최근 로그 (마지막 5 줄):"
        for f in "$BACKEND_LOG" "$FRONTEND_LOG"; do
            [[ -f "$f" ]] && { echo "-- $f --"; tail -n 5 "$f"; echo; }
        done
        ;;
    --backend-only)
        _start_backend; _health_check
        ;;
    --frontend-only)
        _start_frontend
        ;;
    --foreground)
        echo "포어그라운드 모드 — Ctrl+C 로 종료"
        trap 'kill 0' SIGINT SIGTERM
        "$VENV_PY" -m uvicorn backend.main:app \
            --host "$HOST" --port "$BACKEND_PORT" --reload &
        BACKEND_BG=$!
        LINKMIND_API_BASE="http://localhost:$BACKEND_PORT" \
        "$VENV_PY" -m streamlit run frontend/app.py \
            --server.address "$HOST" --server.port "$FRONTEND_PORT" \
            --browser.gatherUsageStats false &
        FRONTEND_BG=$!
        wait $BACKEND_BG $FRONTEND_BG
        ;;
    ""|--background)
        _start_backend
        _start_frontend
        _health_check
        echo
        echo "🌐  backend  : http://localhost:$BACKEND_PORT  (docs: /docs)"
        echo "🎨  frontend : http://localhost:$FRONTEND_PORT"
        echo "📜  로그     : tail -f $BACKEND_LOG $FRONTEND_LOG"
        echo "🛑  정지     : bash scripts/step5_run_dev.sh --stop"
        ;;
    *)
        echo "알 수 없는 옵션: $1"
        echo "사용: $0 [--background|--foreground|--backend-only|--frontend-only|--stop|--status]"
        exit 2
        ;;
esac
