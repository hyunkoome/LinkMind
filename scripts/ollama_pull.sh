#!/usr/bin/env bash
# ============================================================================
# scripts/ollama_pull.sh
# ----------------------------------------------------------------------------
# 이미 떠 있는 linkmind-ollama 컨테이너에 추가 모델을 pull.
#
# 사용:
#   bash scripts/ollama_pull.sh qwen2.5:14b
#   bash scripts/ollama_pull.sh llama3.2:latest
#   bash scripts/ollama_pull.sh nomic-embed-text          # embedding 용
#
# 추천 모델 (RTX 4090 24GB 기준):
#   qwen2.5:7b           — 4.5GB, 빠르고 한국어 OK (기본 추천)
#   qwen2.5:14b          — 9GB, 품질 ↑ 속도 ↓, 여전히 단일 GPU 가능
#   gemma2:9b            — 5.5GB, 대안
#   llama3.2:3b          — 2GB, 매우 빠름 (간단한 작업에)
#   nomic-embed-text     — 274MB, embedding 전용 (768d) — Phase 2 임베딩 백엔드 후보
# ============================================================================
set -euo pipefail

CONTAINER_NAME="linkmind-ollama"

if [ $# -lt 1 ]; then
    sed -n '1,20p' "$0"
    exit 2
fi

MODEL="$1"

if ! docker ps --format '{{.Names}}' | grep -qx "${CONTAINER_NAME}"; then
    echo "❌ ${CONTAINER_NAME} 컨테이너가 실행 중이 아닙니다."
    echo "   먼저: bash scripts/step3_setup_ollama.sh"
    exit 1
fi

echo "📥 ollama pull ${MODEL}"
docker exec -it "${CONTAINER_NAME}" ollama pull "${MODEL}"

echo ""
echo "✅ 완료. 받아져 있는 모델:"
docker exec "${CONTAINER_NAME}" ollama list | sed 's/^/    /'
