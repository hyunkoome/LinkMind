#!/usr/bin/env bash
# step5_run_dev.sh — LinkMind dev 환경 통합 entry (Phase 2.5+, Streamlit 폐기):
#   - 백엔드 FastAPI (:8000, --reload)
#   - 프론트엔드 Next.js 3D graph UI (:3001) — frontend/, Graph + Ingest + Search + Settings
#   - Telegram inbox watcher (Telethon daemon) — env 채워져있고 session 있으면 자동 가동
#
# 모두 idempotent — bash scripts/step5_run_dev.sh 한 명령으로 stop+start 자동.
#
# 사용:
#   bash scripts/step5_run_dev.sh                # 셋 다 백그라운드 (telegram 미설정이면 skip)
#   bash scripts/step5_run_dev.sh --foreground   # 백엔드 포어그라운드 (Ctrl+C 종료)
#   bash scripts/step5_run_dev.sh --backend-only
#   bash scripts/step5_run_dev.sh --frontend-only        # = frontend (Next.js)
#   bash scripts/step5_run_dev.sh --telegram-only
#   bash scripts/step5_run_dev.sh --no-telegram          # backend + frontend 만
#   bash scripts/step5_run_dev.sh --skip-check           # invite 검증 skip (watcher 바로 시작)
#   bash scripts/step5_run_dev.sh --clean-cache          # frontend .next 캐시 강제 정리 후 시작
#   bash scripts/step5_run_dev.sh --stop                 # 셋 다 종료 + vLLM 정지 (GPU VRAM 완전 해제)
#   bash scripts/step5_run_dev.sh --status               # 셋 다 상태 + 최근 로그 tail
#
# vLLM GPU 관리 (2026-06-15):
#   - 시작하면 vLLM 컨테이너(linkmind-vllm + linkmind-vllm-embed)가 내려가 있을 때
#     자동으로 올림 (이미 떠 있으면 no-op). backend 는 LLM/임베딩에 vLLM 이 필요.
#   - --stop 은 vLLM 컨테이너까지 정지해 GPU VRAM 을 완전히 비움 → Docling/학습 등
#     다른 GPU 작업 가능. (이전엔 backend/frontend/telegram 만 멈춰 23GB 가 그대로였음.)
#   - vLLM 자동 기동을 끄려면 LINKMIND_SKIP_VLLM=1 (정지는 항상 수행).
#
# 인프라 컨테이너 (Postgres/Qdrant/Ollama) 가 떠 있어야 함. 죽었으면
# `bash scripts/step2_2_setup_infra.sh` 로 재기동.
#
# turbopack 캐시 자동화 (2026-05-27):
#   - /tmp/next-panic-*.log 가 있으면 turbopack HMR 캐시가 stale 한 상태 → 자동
#     정리 + panic log 도 제거. 큰 파일 삭제/이동 후 다음 시작 시 자동 회복.
#   - 무조건 정리하려면 --clean-cache flag 또는 LINKMIND_CLEAN_FRONTEND_CACHE=1.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

BACKEND_PORT="${LINKMIND_BACKEND_PORT:-8000}"
FRONTEND_PORT="${LINKMIND_FRONTEND_PORT:-3001}"      # Next.js (구 Streamlit 의 자리 차지)
HOST="${LINKMIND_HOST:-127.0.0.1}"
BACKEND_LOG="/tmp/linkmind-backend.log"
FRONTEND_LOG="/tmp/linkmind-frontend.log"
TELEGRAM_LOG="/tmp/telegram-watcher.log"
PID_DIR="/tmp/linkmind-pids"
mkdir -p "$PID_DIR"
BACKEND_PIDFILE="$PID_DIR/backend.pid"
FRONTEND_PIDFILE="$PID_DIR/frontend.pid"
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

# ── vLLM 컨테이너 (GPU VRAM 점유) 관리 ─────────────────────────
# vLLM 은 docker compose --profile vllm 의 별도 영속 서비스(linkmind-vllm,
# linkmind-vllm-embed). step5 가 띄우는 backend/frontend/watcher 와 별개라
# 예전엔 --stop 후에도 GPU 23GB 가 그대로였음. 아래로 라이프사이클을 일원화.
COMPOSE_FILE="$ROOT/compose/docker-compose.dev.yml"
ENV_FILE="$ROOT/env/dev.env"

_compose() {
    docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" --profile vllm "$@"
}

# 현재 떠 있는 vLLM 컨테이너 수 (0~2).
_vllm_running_count() {
    docker ps --format '{{.Names}}' 2>/dev/null \
        | grep -cE '^linkmind-vllm(-embed)?$' || true
}

# vLLM 컨테이너 정지 — GPU VRAM 완전 해제. idempotent (없으면 no-op).
_stop_vllm() {
    if ! command -v docker > /dev/null 2>&1; then
        echo "  · docker 없음 — vLLM 정지 skip"
        return
    fi
    if [[ "$(_vllm_running_count)" == "0" ]]; then
        echo "  · vLLM 컨테이너 이미 정지됨 (GPU VRAM 점유 없음)"
        return
    fi
    echo "  · vLLM 컨테이너 정지 (linkmind-vllm, linkmind-vllm-embed) — GPU VRAM 해제"
    # embed 가 vllm 에 depends_on → 의존 역순(embed 먼저)으로 정지.
    _compose stop vllm-embed vllm 2>/dev/null \
        || docker stop linkmind-vllm-embed linkmind-vllm 2>/dev/null || true
}

# vLLM 컨테이너 보장 — 내려가 있으면 올림 (이미 둘 다 떠 있으면 no-op).
# 모델 로드(특히 Gemma AWQ)는 1~2분 걸리므로 up -d 후 기다리지 않고 바로 리턴
# (backend health check 가 별도로 LLM 준비를 가린다). LINKMIND_SKIP_VLLM=1 로 끔.
_ensure_vllm() {
    if [[ "${LINKMIND_SKIP_VLLM:-0}" == "1" ]]; then
        echo "ℹ️  vLLM 자동 기동 skip (LINKMIND_SKIP_VLLM=1) — LLM/임베딩 비활성"
        return
    fi
    if ! command -v docker > /dev/null 2>&1; then
        echo "⚠️  docker 없음 — vLLM 자동 기동 skip (LLM/임베딩 불가)"
        return
    fi
    if [[ "$(_vllm_running_count)" == "2" ]]; then
        echo "✅ vLLM 이미 가동 중 (linkmind-vllm, linkmind-vllm-embed)"
        return
    fi
    echo "▶️  vLLM 컨테이너 기동 (--profile vllm up -d) — 모델 로드까지 1~2분 warm-up"
    _compose up -d vllm vllm-embed
}

_start_backend() {
    # backend 는 LLM/임베딩에 vLLM 이 필요 → 먼저 vLLM 보장.
    _ensure_vllm
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

# frontend (Next.js 3D graph UI + Settings/Ingest/Search, Phase 2.5+).
# - frontend/ 디렉토리 + npm 둘 다 있어야 가동.
# - node_modules 없으면 첫 1회 npm install 자동 (1-2분, 매번 X).
# - setsid 로 새 process group — npm 의 자식 process tree 까지 안전 정리.
# - turbopack panic 자동 감지 + .next 캐시 자동 정리 (2026-05-27):
#     · /tmp/next-panic-*.log 가 있으면 HMR 캐시가 stale 한 상태 — 파일 큰 삭제/
#       이동 후 흔히 발생. 자동으로 .next 캐시 정리 후 panic log 도 제거.
#     · LINKMIND_CLEAN_FRONTEND_CACHE=1 또는 --clean-cache flag 시 무조건 정리.
_clean_frontend_cache() {
    local dir="$ROOT/frontend"
    if [[ -d "$dir/.next" ]]; then
        local size_mb
        size_mb="$(du -sm "$dir/.next" 2>/dev/null | cut -f1)"
        echo "🧹  frontend .next 캐시 정리 (${size_mb}MB)"
        rm -rf "$dir/.next"
    fi
}

_start_frontend() {
    local dir="$ROOT/frontend"
    if [[ ! -d "$dir" ]]; then
        echo "⚠️  frontend/ 없음 — Next.js UI skip"
        return
    fi
    if ! command -v npm > /dev/null 2>&1; then
        echo "⚠️  frontend skip — npm 없음 (Node 22+ 설치 후 자동 가동)"
        return
    fi
    if _pid_alive "$FRONTEND_PIDFILE"; then
        echo "ℹ️  기존 frontend 정리 후 재기동 (pid=$(cat "$FRONTEND_PIDFILE"))"
        _stop_group "$FRONTEND_PIDFILE" "frontend"
        sleep 1
    fi
    if pgrep -f "next dev -p $FRONTEND_PORT" > /dev/null 2>&1; then
        echo "ℹ️  외부 next dev process 도 정리 (port $FRONTEND_PORT)"
        pkill -f "next dev -p $FRONTEND_PORT" 2>/dev/null || true
        sleep 1
    fi

    # turbopack panic 자동 감지 — /tmp/next-panic-*.log 있으면 HMR 캐시 stale.
    # 자동 정리 + 옛 panic log 도 제거 (다음 panic 감지에 영향 X).
    if compgen -G "/tmp/next-panic-*.log" > /dev/null; then
        echo "⚠️  /tmp/next-panic-*.log 발견 — turbopack 캐시 손상 흔적. 자동 정리."
        _clean_frontend_cache
        rm -f /tmp/next-panic-*.log
    fi
    # 명시적 cache 정리 요청 (env var 또는 --clean-cache flag — main 에서 set).
    if [[ "${LINKMIND_CLEAN_FRONTEND_CACHE:-0}" == "1" ]]; then
        echo "🧹  --clean-cache 또는 LINKMIND_CLEAN_FRONTEND_CACHE=1 — 강제 캐시 정리"
        _clean_frontend_cache
    fi

    if [[ ! -d "$dir/node_modules" ]]; then
        echo "📦  frontend: 첫 npm install (1-2분 소요, 한 번만)…"
        if ! (cd "$dir" && npm install --no-fund --no-audit 2>&1 | tail -10); then
            echo "❌  npm install 실패 — 수동: cd frontend && npm install"
            return
        fi
    fi
    echo "▶️  frontend (next dev) 기동 — :$FRONTEND_PORT, log=$FRONTEND_LOG"
    NEXT_PUBLIC_API_BASE="${NEXT_PUBLIC_API_BASE:-http://localhost:$BACKEND_PORT}" \
    setsid nohup bash -c "cd '$dir' && npm run dev" > "$FRONTEND_LOG" 2>&1 &
    echo $! > "$FRONTEND_PIDFILE"
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

_run_invite_check() {
    # watcher 시작 직전 invite 검증 + 자동 정리 (default). --skip-check 또는
    # SKIP_INVITE_CHECK=1 환경변수로 끔. session 충돌 없음 — watcher 가 죽어있는
    # 시점이라 check 가 session 잡음 → disconnect 후 watcher 가 다음 session 잡음.
    # check 실패해도 watcher 진행 (best effort).
    if [[ "${SKIP_INVITE_CHECK:-0}" == "1" ]]; then
        echo "ℹ️  invite check skip (--skip-check)"
        return 0
    fi
    echo "🔎  invite 검증 + auto-cleanup (yaml/cache 의 죽은 invite 정리)…"
    if "$VENV_PY" -m ai_agents.check_telegram_invites; then
        echo "✅ invite check 완료"
    else
        # exit code != 0 인 경우 — dry-run 에서 정리 대상 발견은 default 모드에선 안 일어남.
        # 따라서 여기 들어오는 건 env 미설정, session lock, telethon error 등 진짜 실패.
        echo "⚠️  invite check 실패 (watcher 는 그대로 진행) — log 확인 권장"
    fi
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
    # invite 검증 + 자동 정리 (watcher session lock 안 잡힌 시점에 안전).
    _run_invite_check
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

# ── arg pre-process — flag (--skip-check) 와 명령 분리 ────────────
# bash case 가 단일 인자 디스패치라 flag 와 명령을 한 줄에 받기 위해 사전 분리.
SKIP_INVITE_CHECK=0
LINKMIND_CLEAN_FRONTEND_CACHE="${LINKMIND_CLEAN_FRONTEND_CACHE:-0}"
POS_ARGS=()
for _a in "$@"; do
    case "$_a" in
        --skip-check) SKIP_INVITE_CHECK=1 ;;
        --clean-cache) LINKMIND_CLEAN_FRONTEND_CACHE=1 ;;
        *) POS_ARGS+=("$_a") ;;
    esac
done
export SKIP_INVITE_CHECK LINKMIND_CLEAN_FRONTEND_CACHE
# 위치 인자 (명령) 만 남겨서 기존 case 로 디스패치.
set -- "${POS_ARGS[@]+"${POS_ARGS[@]}"}"

case "${1:-}" in
    --stop)
        echo "🛑 LinkMind 정지 (vLLM 포함 — GPU VRAM 완전 해제)"
        _stop_one "$BACKEND_PIDFILE" "backend"
        _stop_group "$FRONTEND_PIDFILE" "frontend"
        _stop_telegram
        _stop_vllm
        echo "완료 — GPU 가 비었습니다 (nvidia-smi 로 확인). 다시 켜려면: bash scripts/step5_run_dev.sh"
        ;;
    --status)
        echo "== LinkMind 상태 =="
        if _pid_alive "$BACKEND_PIDFILE"; then
            echo "  ✅ backend  pid=$(cat "$BACKEND_PIDFILE")  :$BACKEND_PORT"
        else
            echo "  ⛔ backend  미가동  :$BACKEND_PORT"
        fi
        if _pid_alive "$FRONTEND_PIDFILE"; then
            echo "  ✅ frontend pid=$(cat "$FRONTEND_PIDFILE")  :$FRONTEND_PORT  (Next.js)"
        else
            echo "  ⛔ frontend 미가동  :$FRONTEND_PORT  (Next.js)"
        fi
        if _pid_alive "$TELEGRAM_PIDFILE"; then
            echo "  ✅ telegram pid=$(cat "$TELEGRAM_PIDFILE")  inbox listening"
        else
            echo "  ⛔ telegram 미가동  inbox"
        fi
        if command -v docker > /dev/null 2>&1; then
            case "$(_vllm_running_count)" in
                2) echo "  ✅ vLLM     linkmind-vllm + linkmind-vllm-embed (GPU 점유 중)" ;;
                1) echo "  ⚠️  vLLM     1/2 만 가동 — bash scripts/step5_run_dev.sh 로 복구" ;;
                *) echo "  ⛔ vLLM     미가동  (GPU VRAM 해제 상태)" ;;
            esac
        fi
        echo
        echo "최근 로그 (마지막 5 줄):"
        for f in "$BACKEND_LOG" "$FRONTEND_LOG" "$TELEGRAM_LOG"; do
            [[ -f "$f" ]] && { echo "-- $f --"; tail -n 5 "$f"; echo; }
        done
        ;;
    --backend-only)
        _start_backend; _health_check
        ;;
    --frontend-only)
        _start_frontend
        ;;
    --telegram-only)
        _start_telegram
        ;;
    --no-telegram)
        _start_backend
        _start_frontend
        _health_check
        echo
        echo "🌐  backend  : http://localhost:$BACKEND_PORT  (docs: /docs)"
        echo "🎨  frontend : http://localhost:$FRONTEND_PORT  (Next.js)"
        echo "📜  로그     : tail -f $BACKEND_LOG $FRONTEND_LOG"
        echo "🛑  정지     : bash scripts/step5_run_dev.sh --stop"
        ;;
    --foreground)
        echo "포어그라운드 모드 — Ctrl+C 로 종료 (telegram + frontend 는 background)"
        _ensure_vllm
        _start_telegram
        _start_frontend
        trap 'kill 0' SIGINT SIGTERM
        "$VENV_PY" -m uvicorn backend.main:app \
            --host "$HOST" --port "$BACKEND_PORT" --reload
        ;;
    ""|--background)
        _start_backend
        _start_frontend
        _start_telegram
        _health_check
        echo
        echo "🌐  backend  : http://localhost:$BACKEND_PORT  (docs: /docs)"
        echo "🎨  frontend : http://localhost:$FRONTEND_PORT  (Graph · Ingest · Search · Settings)"
        echo "📨  telegram : LinkMind-Inbox 채널 listening (log: $TELEGRAM_LOG)"
        echo "📜  로그     : tail -f $BACKEND_LOG $FRONTEND_LOG $TELEGRAM_LOG"
        echo "🛑  정지     : bash scripts/step5_run_dev.sh --stop"
        ;;
    *)
        echo "알 수 없는 옵션: $1"
        echo "사용: $0 [--background|--foreground|--backend-only|--frontend-only|--telegram-only|--no-telegram|--stop|--status] [--skip-check] [--clean-cache]"
        exit 2
        ;;
esac
