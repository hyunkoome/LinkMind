#!/usr/bin/env bash
# scripts/slack_ingest_all.sh — slackdump export 전체를 LinkMind 로 backfill.
#
# Phase C wave-2 (2026-05-19~) 일회성. uvicorn 의 bge-m3 임베딩과 GPU OOM 충돌
# 우려가 있어, 실행 전 backend 종료 자동 점검. vLLM 컨테이너는 그대로 둔 채.
#
# 사용:
#   bash scripts/slack_ingest_all.sh                                    # latest symlink + 기본 workspace
#   bash scripts/slack_ingest_all.sh archive/slack_export/full_2026-05-19_20-04-58
#   SLACK_WORKSPACE_URL=https://hkkim.slack.com bash scripts/slack_ingest_all.sh
#   bash scripts/slack_ingest_all.sh --force                            # 동일 hash 도 재요약
#   bash scripts/slack_ingest_all.sh --channel 가-공부-cuda-programming    # 단일 채널만
#
# 진행률: tqdm 으로 메시지 단위 표시. log 는 /tmp/linkmind-slack-ingest-*.log.
# ingest 중에는 backend/frontend 비가동 — 끝나면 `bash scripts/step5_run_dev.sh` 로 재기동.

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# ── 기본값 + arg 파싱 ────────────────────────────────────────
EXPORT_DIR="archive/slack_export/latest"
WORKSPACE_URL="${SLACK_WORKSPACE_URL:-https://w1710672365-sjj477000.slack.com}"
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --force)
            EXTRA_ARGS+=("--force")
            shift ;;
        --channel)
            EXTRA_ARGS+=("--channel" "$2")
            shift 2 ;;
        --no-progress)
            EXTRA_ARGS+=("--no-progress")
            shift ;;
        -h|--help)
            sed -n '2,15p' "$0"
            exit 0 ;;
        archive/*|/*)
            EXPORT_DIR="$1"
            shift ;;
        *)
            echo "알 수 없는 옵션: $1"
            echo "사용: $0 [export_dir] [--channel <name>] [--force] [--no-progress]"
            exit 2 ;;
    esac
done

# ── 사전 점검 ───────────────────────────────────────────────
[[ -d "$EXPORT_DIR" ]] || { echo "❌ export 디렉토리가 없음: $EXPORT_DIR"; exit 1; }
[[ -f "$EXPORT_DIR/channels.json" ]] || {
    echo "❌ channels.json 없음 — slackdump standard export 가 아님: $EXPORT_DIR"; exit 1; }

# 1) backend uvicorn (bge-m3 임베딩) 가동 중이면 OOM 위험 — 사용자 확인.
if curl -s --max-time 2 "http://localhost:8000/health" > /dev/null 2>&1; then
    echo "⚠️  backend uvicorn (:8000) 가동 중 — bge-m3 가 GPU 점유 → CLI ingest 가 OOM 가능"
    echo "    권장: bash scripts/step5_run_dev.sh --stop  (vLLM 컨테이너는 별도라 안 꺼짐)"
    read -r -p "그래도 진행? (y/N) " ans
    [[ "$ans" == "y" || "$ans" == "Y" ]] || { echo "취소"; exit 1; }
fi

# 2) GPU 여유 메모리 (CLI 임베딩 ~1500 MiB 필요).
if command -v nvidia-smi > /dev/null 2>&1; then
    free_mb=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits | head -1)
    echo "📊 GPU 여유: ${free_mb} MiB (임베딩 ~1500 MiB 필요)"
    if [[ "$free_mb" -lt 1500 ]]; then
        echo "⚠️  여유 부족 가능 — 큰 채널에서 OOM 발생 시 즉시 중단 후 backend 종료"
    fi
fi

# 3) vLLM 컨테이너 살아있나 (요약 LLM).
if ! curl -s --max-time 2 "http://localhost:8001/v1/models" > /dev/null 2>&1; then
    echo "⚠️  vLLM (:8001) 응답 없음 — 요약은 fallback (Ollama 또는 skip) 으로 갈 수 있음."
    echo "    docker compose ps 로 컨테이너 상태 확인 권장."
fi

# ── 실행 ────────────────────────────────────────────────────
TS=$(date +%Y%m%d-%H%M%S)
LOG="/tmp/linkmind-slack-ingest-${TS}.log"

echo
echo "▶️  Slack ingest 시작"
echo "    export       : $EXPORT_DIR"
echo "    workspace_url: $WORKSPACE_URL"
echo "    extra        : ${EXTRA_ARGS[*]:-(없음)}"
echo "    log          : $LOG"
echo "    issues       : <export_dir 부모>/issues/<ts>/manifest.json (CLI 기본값)"
echo "    중단: Ctrl+C — idempotent (이미 ingest 된 메시지는 hash dedup 으로 skip)"
echo

# tqdm 진행률은 stderr — 화면에 보이고 log 에도 남음.
# issues manifest 는 CLI 가 자동으로 archive/slack_export/issues/<ts>/ 에 저장.
.venv/bin/python -m backend.ingest.slack "$EXPORT_DIR" \
    --workspace-url "$WORKSPACE_URL" \
    "${EXTRA_ARGS[@]}" 2>&1 | tee "$LOG"

echo
echo "✅ 완료 — log: $LOG"
echo "🌐 다시 backend/frontend 띄우려면: bash scripts/step5_run_dev.sh"
