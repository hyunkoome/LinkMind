#!/usr/bin/env bash
# ============================================================================
# scripts/ollama_chat.sh
# ----------------------------------------------------------------------------
# linkmind-ollama 컨테이너의 모델로 한 번의 프롬프트를 보내고 응답을 출력.
# 디버깅/검증용 — 정상 흐름은 LinkMind FastAPI 의 /ask 엔드포인트가 담당.
#
# 사용:
#   bash scripts/ollama_chat.sh "안녕, 너의 모델 이름은?"
#   bash scripts/ollama_chat.sh "transformer 가 뭐야?" qwen2.5:14b
#
# 인자:
#   $1  프롬프트 (필수)
#   $2  모델명 (선택; 기본은 env/dev.env 의 OLLAMA_MODEL)
# ============================================================================
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${PROJECT_ROOT}/env/dev.env"

if [ $# -lt 1 ]; then
    sed -n '1,17p' "$0"
    exit 2
fi

PROMPT="$1"
MODEL="${2:-}"

if [ -z "$MODEL" ] && [ -f "$ENV_FILE" ]; then
    MODEL=$(grep -E '^OLLAMA_MODEL=' "$ENV_FILE" | head -1 | cut -d= -f2- || true)
fi
[ -z "$MODEL" ] && MODEL="qwen2.5:7b"

echo "🦙 model: ${MODEL}"
echo "💬 prompt: ${PROMPT}"
echo ""

# JSON 안전한 프롬프트 인코딩
PAYLOAD=$(python3 -c "import json,sys; print(json.dumps({'model':sys.argv[1],'messages':[{'role':'user','content':sys.argv[2]}],'stream':False}))" "$MODEL" "$PROMPT")

curl -fsS -X POST http://localhost:11434/api/chat \
    -H 'Content-Type: application/json' \
    -d "$PAYLOAD" \
| python3 -c 'import sys,json
d=json.load(sys.stdin)
msg=d.get("message",{}).get("content","(no response)")
print("--- 답변 ---")
print(msg)
print()
print("--- 통계 ---")
for k in ("model","total_duration","load_duration","prompt_eval_count","eval_count","eval_duration"):
    v=d.get(k)
    if v is not None: print(f"  {k}: {v}")'
