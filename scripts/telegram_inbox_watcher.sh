#!/usr/bin/env bash
# scripts/telegram_inbox_watcher.sh — Telethon inbox watcher 의 bash wrapper.
#
# 첫 실행:
#   bash scripts/telegram_inbox_watcher.sh
#   → 전화번호 + SMS 코드 입력 (대화식). 그 후 volumes/telegram/inbox.session 자동 생성.
#
# 백필 + listen:
#   bash scripts/telegram_inbox_watcher.sh --backfill 50
#
# 백그라운드 daemon:
#   bash scripts/telegram_inbox_watcher.sh --daemon
#   → /tmp/telegram-watcher.log 로 출력 + pid 파일 /tmp/telegram-watcher.pid
#   → 종료: bash scripts/telegram_inbox_watcher.sh --stop

set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PY="$ROOT/.venv/bin/python"
[[ -x "$PY" ]] || { echo "❌ .venv 없음 — bash scripts/step1_install_base_env.sh 먼저"; exit 1; }

LOG="${TELEGRAM_WATCHER_LOG:-/tmp/telegram-watcher.log}"
PIDF="${TELEGRAM_WATCHER_PID:-/tmp/telegram-watcher.pid}"

case "${1:-}" in
    --stop)
        if [[ -f "$PIDF" ]] && kill -0 "$(cat "$PIDF")" 2>/dev/null; then
            echo "🛑 Telethon watcher 종료 (pid=$(cat "$PIDF"))"
            kill "$(cat "$PIDF")" || true
            rm -f "$PIDF"
        else
            echo "ℹ️  실행 중인 watcher 없음."
        fi
        ;;
    --status)
        if [[ -f "$PIDF" ]] && kill -0 "$(cat "$PIDF")" 2>/dev/null; then
            echo "✅ watcher 가동 중 (pid=$(cat "$PIDF"))"
            echo "   로그: tail -f $LOG"
        else
            echo "⛔ watcher 미가동"
        fi
        ;;
    --daemon)
        shift
        nohup "$PY" scripts/telegram_inbox_watcher.py "$@" > "$LOG" 2>&1 &
        echo $! > "$PIDF"
        disown 2>/dev/null || true
        echo "▶️  daemon 기동 (pid=$(cat "$PIDF"), log=$LOG)"
        echo "   첫 실행이면 인증 대화가 필요 — daemon 대신 foreground 로 한 번:"
        echo "   bash scripts/telegram_inbox_watcher.sh   (Ctrl+C 로 종료)"
        ;;
    *)
        exec "$PY" scripts/telegram_inbox_watcher.py "$@"
        ;;
esac
