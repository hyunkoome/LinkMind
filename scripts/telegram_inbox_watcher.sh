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
#   → 기존 watcher 가 있으면 자동 정리 후 새로 띄움 (idempotent)
#   → /tmp/telegram-watcher.log 로 출력 + pid 파일 /tmp/telegram-watcher.pid
#   → 종료: bash scripts/telegram_inbox_watcher.sh --stop
#
# 강제 재기동 (alias):
#   bash scripts/telegram_inbox_watcher.sh --restart
#
# 상태/로그:
#   bash scripts/telegram_inbox_watcher.sh --status
#   tail -f /tmp/telegram-watcher.log

set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PY="$ROOT/.venv/bin/python"
[[ -x "$PY" ]] || { echo "❌ .venv 없음 — bash scripts/step1_install_base_env.sh 먼저"; exit 1; }

LOG="${TELEGRAM_WATCHER_LOG:-/tmp/telegram-watcher.log}"
PIDF="${TELEGRAM_WATCHER_PID:-/tmp/telegram-watcher.pid}"

case "${1:-}" in
    --stop)
        # pidfile 기반 (daemon 으로 띄운 것) + name 기반 (foreground 로 띄웠던 것)
        # 모두 정리. pkill 의 -f 는 cmdline 매칭이라 LinkMind 의 watcher 만 잡음.
        if [[ -f "$PIDF" ]] && kill -0 "$(cat "$PIDF")" 2>/dev/null; then
            echo "🛑 daemon 종료 (pid=$(cat "$PIDF"))"
            kill "$(cat "$PIDF")" || true
        fi
        rm -f "$PIDF"
        if pgrep -f "scripts/telegram_inbox_watcher.py" > /dev/null 2>&1; then
            echo "🛑 그 외 watcher process 도 정리"
            pkill -f "scripts/telegram_inbox_watcher.py" || true
            sleep 1
            pkill -9 -f "scripts/telegram_inbox_watcher.py" 2>/dev/null || true
        fi
        echo "완료 — 남은 watcher process:"
        pgrep -fl "scripts/telegram_inbox_watcher.py" 2>/dev/null || echo "  (없음)"
        ;;
    --status)
        if [[ -f "$PIDF" ]] && kill -0 "$(cat "$PIDF")" 2>/dev/null; then
            echo "✅ watcher 가동 중 (pid=$(cat "$PIDF"))"
            echo "   로그: tail -f $LOG"
        else
            echo "⛔ watcher 미가동"
        fi
        ;;
    --daemon|--restart)
        # idempotent — 기존 watcher process 가 도는 중이면 정리하고 새로 띄움.
        # 같은 채널을 두 watcher 가 동시 listen 하면 race (dedup 으로 안전하지만 낭비).
        if pgrep -f "scripts/telegram_inbox_watcher.py" > /dev/null 2>&1; then
            echo "ℹ️  기존 watcher 정리 중…"
            pkill -f "scripts/telegram_inbox_watcher.py" || true
            sleep 1
            pkill -9 -f "scripts/telegram_inbox_watcher.py" 2>/dev/null || true
            rm -f "$PIDF"
        fi
        shift
        nohup "$PY" scripts/telegram_inbox_watcher.py "$@" > "$LOG" 2>&1 &
        echo $! > "$PIDF"
        disown 2>/dev/null || true
        sleep 1
        echo "▶️  daemon 기동 (pid=$(cat "$PIDF"), log=$LOG)"
        echo "   첫 실행이면 인증 대화가 필요 — daemon 대신 foreground 로 한 번:"
        echo "   bash scripts/telegram_inbox_watcher.sh   (Ctrl+C 로 종료)"
        ;;
    *)
        # foreground — 기존 daemon 이 도는 중이면 경고 (race 위험).
        if pgrep -f "scripts/telegram_inbox_watcher.py" > /dev/null 2>&1; then
            echo "⚠️  기존 watcher process 가 있음 — 같이 돌면 race 위험."
            echo "   먼저 정리하려면:  bash scripts/telegram_inbox_watcher.sh --stop"
            echo "   또는 자동 정리:  bash scripts/telegram_inbox_watcher.sh --restart"
            echo "   3초 후 계속…"
            sleep 3
        fi
        exec "$PY" scripts/telegram_inbox_watcher.py "$@"
        ;;
esac
