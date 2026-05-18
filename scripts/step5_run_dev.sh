#!/usr/bin/env bash
# step5_run_dev.sh — LinkMind dev 환경 통합 entry:
#   - 백엔드 FastAPI (:8000, --reload)
#   - 프론트엔드 Streamlit (:8501)
#   - 프론트엔드 v2 Next.js 3D graph UI (:3001) — frontend_v2/ 있고 npm 있으면 자동 (Phase 2.5+)
#   - Telegram inbox watcher (Telethon daemon) — env 채워져있고 session 있으면 자동 가동
#
# 사용:
#   bash scripts/step5_run_dev.sh                # 넷 다 백그라운드 (frontend_v2/telegram 미설정이면 skip)
#   bash scripts/step5_run_dev.sh --foreground   # 백/프론트 포어그라운드 (Ctrl+C 종료)
#   bash scripts/step5_run_dev.sh --backend-only
#   bash scripts/step5_run_dev.sh --frontend-only
#   bash scripts/step5_run_dev.sh --frontend-v2-only
#   bash scripts/step5_run_dev.sh --telegram-only
#   bash scripts/step5_run_dev.sh --no-telegram    # backend + frontend + frontend_v2 만
#   bash scripts/step5_run_dev.sh --no-frontend-v2 # backend + Streamlit + telegram 만
#   bash scripts/step5_run_dev.sh --stop          # 넷 다 종료
#   bash scripts/step5_run_dev.sh --status        # 넷 다 상태 + 최근 로그 tail
#
# 인프라 컨테이너 (Postgres/Qdrant/Ollama) 가 떠 있어야 함. 죽었으면
# `bash scripts/step2_2_setup_infra.sh` 로 재기동.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

BACKEND_PORT="${LINKMIND_BACKEND_PORT:-8000}"
FRONTEND_PORT="${LINKMIND_FRONTEND_PORT:-8501}"
FRONTEND_V2_PORT="${LINKMIND_FRONTEND_V2_PORT:-3001}"
HOST="${LINKMIND_HOST:-127.0.0.1}"
BACKEND_LOG="/tmp/linkmind-backend.log"
FRONTEND_LOG="/tmp/linkmind-frontend.log"
FRONTEND_V2_LOG="/tmp/linkmind-frontend-v2.log"
TELEGRAM_LOG="/tmp/telegram-watcher.log"
PID_DIR="/tmp/linkmind-pids"
mkdir -p "$PID_DIR"
BACKEND_PIDFILE="$PID_DIR/backend.pid"
FRONTEND_PIDFILE="$PID_DIR/frontend.pid"
FRONTEND_V2_PIDFILE="$PID_DIR/frontend-v2.pid"
# Telegram watcher 는 자체 pidfile 사용 (telegram_inbox_watcher.sh 와 일관성)
TELEGRAM_PIDFILE="/tmp/telegram-watcher.pid"

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

# process group 통째로 종료 — npm/next 처럼 자식 process tree 가 깊은 경우 사용.
# setsid 로 띄운 process 의 PGID = PID. kill -- -PGID 로 group leader 포함 전체 정리.
_stop_group() {
    local f="$1"
    local name="$2"
    if [[ -f "$f" ]]; then
        local pid; pid="$(cat "$f")"
        if kill -0 "$pid" 2>/dev/null; then
            echo "  · $name (pgid=$pid) 종료 (tree)"
            kill -- "-$pid" 2>/dev/null || true
            sleep 1
            kill -9 -- "-$pid" 2>/dev/null || true
        fi
    fi
    rm -f "$f"
}

_start_backend() {
    # idempotent — 이미 가동 중이면 정리 후 새로 띄움 (코드 변경 반영 + cache flush).
    if _pid_alive "$BACKEND_PIDFILE"; then
        echo "ℹ️  기존 backend 정리 후 재기동 (pid=$(cat "$BACKEND_PIDFILE"))"
        _stop_one "$BACKEND_PIDFILE" "backend"
        sleep 1
    fi
    echo "▶️  backend (uvicorn) 기동 — :$BACKEND_PORT, log=$BACKEND_LOG"
    nohup "$VENV_PY" -m uvicorn backend.main:app \
        --host "$HOST" --port "$BACKEND_PORT" --reload \
        > "$BACKEND_LOG" 2>&1 &
    echo $! > "$BACKEND_PIDFILE"
    disown 2>/dev/null || true
}

_start_frontend() {
    # idempotent — 동일.
    if _pid_alive "$FRONTEND_PIDFILE"; then
        echo "ℹ️  기존 frontend 정리 후 재기동 (pid=$(cat "$FRONTEND_PIDFILE"))"
        _stop_one "$FRONTEND_PIDFILE" "frontend"
        sleep 1
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

# frontend_v2 (Next.js 3D graph UI, Phase 2.5+).
# - frontend_v2/ 디렉토리 + npm 둘 다 있어야 가동. 둘 중 하나라도 없으면 silent skip.
# - node_modules 없으면 첫 1회 npm install 자동 (1-2분, foreground 메시지).
# - setsid 로 새 process group — npm 의 자식 process tree 까지 안전 정리 가능.
_start_frontend_v2() {
    local dir="$ROOT/frontend_v2"
    if [[ ! -d "$dir" ]]; then
        return  # frontend_v2 디렉토리 없음 → skip (silent)
    fi
    if ! command -v npm > /dev/null 2>&1; then
        echo "ℹ️  frontend_v2 skip — npm 없음 (Node 22+ 설치 후 자동 가동)"
        return
    fi
    # idempotent
    if _pid_alive "$FRONTEND_V2_PIDFILE"; then
        echo "ℹ️  기존 frontend_v2 정리 후 재기동 (pid=$(cat "$FRONTEND_V2_PIDFILE"))"
        _stop_group "$FRONTEND_V2_PIDFILE" "frontend_v2"
        sleep 1
    fi
    # 다른 process 가 같은 port 잡고 있으면 정리 (외부에서 띄운 npm run dev 등)
    if pgrep -f "next dev -p $FRONTEND_V2_PORT" > /dev/null 2>&1; then
        echo "ℹ️  외부 next dev process 도 정리 (port $FRONTEND_V2_PORT)"
        pkill -f "next dev -p $FRONTEND_V2_PORT" 2>/dev/null || true
        sleep 1
    fi
    # 첫 npm install (node_modules 없을 때만)
    if [[ ! -d "$dir/node_modules" ]]; then
        echo "📦  frontend_v2: 첫 npm install 실행 (1-2분 소요, 한 번만)…"
        if ! (cd "$dir" && npm install --no-fund --no-audit 2>&1 | tail -10); then
            echo "❌  npm install 실패 — log 확인 후 수동: cd frontend_v2 && npm install"
            return
        fi
    fi
    echo "▶️  frontend_v2 (next dev) 기동 — :$FRONTEND_V2_PORT, log=$FRONTEND_V2_LOG"
    # setsid 로 새 session/process group — kill 시 npm + node + children 다 정리
    NEXT_PUBLIC_API_BASE="${NEXT_PUBLIC_API_BASE:-http://localhost:$BACKEND_PORT}" \
    setsid nohup bash -c "cd '$dir' && npm run dev" > "$FRONTEND_V2_LOG" 2>&1 &
    echo $! > "$FRONTEND_V2_PIDFILE"
    disown 2>/dev/null || true
}

_telegram_env_ready() {
    # env/dev.env 의 TELEGRAM_API_ID/HASH 와 session 파일 존재 — auto-start 조건.
    # bash 가 env 파일 source 안 함 (다른 시크릿 노출 X). grep 으로 직접 검사.
    local api_id="" api_hash="" session="volumes/telegram/inbox.session"
    if [[ -f env/dev.env ]]; then
        api_id="$(grep -E '^TELEGRAM_API_ID=' env/dev.env | head -1 | cut -d= -f2-)"
        api_hash="$(grep -E '^TELEGRAM_API_HASH=' env/dev.env | head -1 | cut -d= -f2-)"
        local cfg_session
        cfg_session="$(grep -E '^TELEGRAM_SESSION_PATH=' env/dev.env | head -1 | cut -d= -f2-)"
        [[ -n "$cfg_session" ]] && session="$cfg_session"
    fi
    [[ -n "$api_id" && -n "$api_hash" && -f "$session" ]]
}

_start_telegram() {
    if ! _telegram_env_ready; then
        echo "ℹ️  telegram watcher skip — TELEGRAM_API_ID/HASH 또는 session 미설정"
        echo "    준비:  docs/telegram_setup.md"
        echo "    첫 인증: bash ai_agents/telegram_inbox_watcher.sh (foreground, SMS 입력)"
        return
    fi
    # idempotent — pidfile + 외부 watcher process 모두 정리 후 새로 띄움 (race 방지).
    if _pid_alive "$TELEGRAM_PIDFILE" || pgrep -f "ai_agents/telegram_inbox_watcher\.py" > /dev/null 2>&1; then
        echo "ℹ️  기존 telegram watcher 정리 후 재기동"
        _stop_telegram
        sleep 1
    fi
    echo "▶️  telegram watcher 기동 — log=$TELEGRAM_LOG"
    nohup "$VENV_PY" ai_agents/telegram_inbox_watcher.py > "$TELEGRAM_LOG" 2>&1 &
    echo $! > "$TELEGRAM_PIDFILE"
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

_stop_telegram() {
    # telegram watcher 는 자체 pidfile + 이름 매칭. step5 가 띄운 게 아닌 외부에서
    # 띄운 watcher (예: ai_agents/telegram_inbox_watcher.sh --daemon) 도 같이 정리.
    if _pid_alive "$TELEGRAM_PIDFILE"; then
        echo "  · telegram (pid=$(cat "$TELEGRAM_PIDFILE")) 종료"
        kill "$(cat "$TELEGRAM_PIDFILE")" 2>/dev/null || true
        sleep 1
        kill -9 "$(cat "$TELEGRAM_PIDFILE")" 2>/dev/null || true
    fi
    if pgrep -f "ai_agents/telegram_inbox_watcher.py" > /dev/null 2>&1; then
        echo "  · 외부 watcher process 도 정리"
        pkill -f "ai_agents/telegram_inbox_watcher.py" 2>/dev/null || true
    fi
    rm -f "$TELEGRAM_PIDFILE"
}

case "${1:-}" in
    --stop)
        echo "🛑 LinkMind 정지"
        _stop_one "$BACKEND_PIDFILE" "backend"
        _stop_one "$FRONTEND_PIDFILE" "frontend"
        _stop_group "$FRONTEND_V2_PIDFILE" "frontend_v2"
        _stop_telegram
        echo "완료"
        ;;
    --status)
        echo "== LinkMind 상태 =="
        for pair in "backend     $BACKEND_PIDFILE     $BACKEND_LOG     :$BACKEND_PORT" \
                    "frontend    $FRONTEND_PIDFILE    $FRONTEND_LOG    :$FRONTEND_PORT" \
                    "frontend_v2 $FRONTEND_V2_PIDFILE $FRONTEND_V2_LOG :$FRONTEND_V2_PORT" \
                    "telegram    $TELEGRAM_PIDFILE    $TELEGRAM_LOG    inbox"; do
            read -r name pidf log port <<< "$pair"
            if _pid_alive "$pidf"; then
                echo "  ✅ $name pid=$(cat "$pidf") $port"
            else
                echo "  ⛔ $name 미가동 ($port)"
            fi
        done
        echo
        echo "최근 로그 (마지막 5 줄):"
        for f in "$BACKEND_LOG" "$FRONTEND_LOG" "$FRONTEND_V2_LOG" "$TELEGRAM_LOG"; do
            [[ -f "$f" ]] && { echo "-- $f --"; tail -n 5 "$f"; echo; }
        done
        ;;
    --backend-only)
        _start_backend; _health_check
        ;;
    --frontend-only)
        _start_frontend
        ;;
    --frontend-v2-only)
        _start_frontend_v2
        ;;
    --telegram-only)
        _start_telegram
        ;;
    --no-telegram)
        _start_backend
        _start_frontend
        _start_frontend_v2
        _health_check
        echo
        echo "🌐  backend     : http://localhost:$BACKEND_PORT  (docs: /docs)"
        echo "🎨  frontend    : http://localhost:$FRONTEND_PORT  (Streamlit)"
        echo "🔮  frontend_v2 : http://localhost:$FRONTEND_V2_PORT  (3D graph UI)"
        echo "📜  로그        : tail -f $BACKEND_LOG $FRONTEND_LOG $FRONTEND_V2_LOG"
        echo "🛑  정지        : bash scripts/step5_run_dev.sh --stop"
        ;;
    --no-frontend-v2)
        _start_backend
        _start_frontend
        _start_telegram
        _health_check
        echo
        echo "🌐  backend  : http://localhost:$BACKEND_PORT  (docs: /docs)"
        echo "🎨  frontend : http://localhost:$FRONTEND_PORT  (Streamlit)"
        echo "📨  telegram : LinkMind-Inbox 채널 listening (log: $TELEGRAM_LOG)"
        echo "📜  로그     : tail -f $BACKEND_LOG $FRONTEND_LOG $TELEGRAM_LOG"
        echo "🛑  정지     : bash scripts/step5_run_dev.sh --stop"
        ;;
    --foreground)
        echo "포어그라운드 모드 — Ctrl+C 로 종료 (telegram + frontend_v2 는 background)"
        _start_telegram
        _start_frontend_v2
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
        _start_frontend_v2
        _start_telegram
        _health_check
        echo
        echo "🌐  backend     : http://localhost:$BACKEND_PORT  (docs: /docs)"
        echo "🎨  frontend    : http://localhost:$FRONTEND_PORT  (Streamlit, 기존 Settings/Ingest)"
        echo "🔮  frontend_v2 : http://localhost:$FRONTEND_V2_PORT  (3D graph UI, Phase 2.5+)"
        echo "📨  telegram    : LinkMind-Inbox 채널 listening (log: $TELEGRAM_LOG)"
        echo "📜  로그        : tail -f $BACKEND_LOG $FRONTEND_LOG $FRONTEND_V2_LOG $TELEGRAM_LOG"
        echo "🛑  정지        : bash scripts/step5_run_dev.sh --stop"
        ;;
    *)
        echo "알 수 없는 옵션: $1"
        echo "사용: $0 [--background|--foreground|--backend-only|--frontend-only|--frontend-v2-only|--telegram-only|--no-telegram|--no-frontend-v2|--stop|--status]"
        exit 2
        ;;
esac
