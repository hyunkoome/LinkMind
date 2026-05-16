#!/usr/bin/env bash
# ============================================================================
# scripts/step3_check_ollama.sh
# ----------------------------------------------------------------------------
# step3_setup_ollama.sh 로 셋업된 Ollama 가 정상 동작하는지 검증.
#
# 점검 항목:
#   1. linkmind-ollama 컨테이너 healthy
#   2. Ollama API (/api/tags) 응답
#   3. env/dev.env 의 OLLAMA_MODEL 이 컨테이너 안에 존재
#   4. 간단한 generate 호출 성공 (1-token 짜리 dry run)
#   5. (옵션) GPU 가속 동작 — nvidia-smi 가 ollama 프로세스 잡고 있는지
#
# 사용:
#   bash scripts/step3_check_ollama.sh
#
# 종료 코드:
#   0  모든 필수 체크 통과
#   1  하나 이상의 필수 체크 실패
# ============================================================================
set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${PROJECT_ROOT}/env/dev.env"
CONTAINER_NAME="linkmind-ollama"

PASS=0
WARN=0
FAIL=0
HAS_TTY=0
[ -t 1 ] && HAS_TTY=1

green()  { if [ "$HAS_TTY" -eq 1 ]; then printf '\033[32m%s\033[0m' "$*"; else printf '%s' "$*"; fi; }
yellow() { if [ "$HAS_TTY" -eq 1 ]; then printf '\033[33m%s\033[0m' "$*"; else printf '%s' "$*"; fi; }
red()    { if [ "$HAS_TTY" -eq 1 ]; then printf '\033[31m%s\033[0m' "$*"; else printf '%s' "$*"; fi; }

ok()   { printf '  %s  %s\n' "$(green '✅')" "$*"; PASS=$((PASS+1)); }
warn() { printf '  %s  %s\n' "$(yellow '⚠️ ')" "$*"; WARN=$((WARN+1)); }
fail() { printf '  %s  %s\n' "$(red '❌')" "$*"; FAIL=$((FAIL+1)); }

# ---- env 로드 -------------------------------------------------------------
if [ -f "$ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    . "$ENV_FILE"
    set +a
fi
OLLAMA_MODEL="${OLLAMA_MODEL:-qwen2.5:7b}"
OLLAMA_PORT="${OLLAMA_PORT:-11434}"

echo "🔍 Ollama 점검 (모델: ${OLLAMA_MODEL})"
echo ""

# ---- 1. 컨테이너 healthy ---------------------------------------------------
echo "[1] linkmind-ollama 컨테이너"
CID="$(docker ps -aq --filter "name=^${CONTAINER_NAME}$" 2>/dev/null || true)"
if [ -z "$CID" ]; then
    fail "${CONTAINER_NAME} 컨테이너 없음 — 'bash scripts/step2_2_setup_infra.sh' 먼저"
    echo ""; echo "💥 컨테이너 없음 — 중단"; exit 1
fi
HEALTH="$(docker inspect -f '{{.State.Health.Status}}' "$CID" 2>/dev/null || echo unknown)"
RUNNING="$(docker inspect -f '{{.State.Running}}' "$CID" 2>/dev/null || echo unknown)"
if [ "$HEALTH" = "healthy" ]; then
    ok "컨테이너 healthy (running=${RUNNING})"
else
    fail "컨테이너 상태: health=${HEALTH} running=${RUNNING}"
fi

# ---- 2. API 응답 -----------------------------------------------------------
echo ""
echo "[2] Ollama API (/api/tags, port ${OLLAMA_PORT})"
if ! command -v curl >/dev/null 2>&1; then
    warn "curl 없음 — API 검증 생략"
else
    TAGS_JSON="$(curl -fsS "http://127.0.0.1:${OLLAMA_PORT}/api/tags" 2>/dev/null || true)"
    if [ -z "$TAGS_JSON" ]; then
        fail "/api/tags 응답 없음"
        echo ""; echo "💥 API 미응답 — 중단"; exit 1
    else
        ok "/api/tags 응답 OK"
    fi
fi

# ---- 3. OLLAMA_MODEL 존재 -------------------------------------------------
echo ""
echo "[3] env 의 OLLAMA_MODEL='${OLLAMA_MODEL}' pull 여부"
if [ -n "${TAGS_JSON:-}" ]; then
    # ":" 가 들어간 이름을 안전하게 매칭
    if printf '%s' "$TAGS_JSON" | grep -Fq "\"${OLLAMA_MODEL}\""; then
        ok "${OLLAMA_MODEL} 존재"
    else
        fail "${OLLAMA_MODEL} 가 컨테이너에 없음 — 'bash scripts/step3_setup_ollama.sh' 로 pull"
    fi
else
    warn "TAGS_JSON 비어있음 — 모델 존재 확인 생략"
fi

# ---- 4. generate 한 번 호출 ------------------------------------------------
echo ""
echo "[4] /api/generate dry run (num_predict=1)"
if command -v curl >/dev/null 2>&1; then
    # stream=false + num_predict=1 로 빠른 응답.
    GEN_OUT="$(curl -fsS -X POST "http://127.0.0.1:${OLLAMA_PORT}/api/generate" \
        -H 'content-type: application/json' \
        -d "{\"model\":\"${OLLAMA_MODEL}\",\"prompt\":\"ping\",\"stream\":false,\"options\":{\"num_predict\":1}}" \
        2>/dev/null || true)"
    if printf '%s' "$GEN_OUT" | grep -q '"response"'; then
        ok "generate 응답 OK"
    else
        fail "generate 호출 실패 — 응답: $(printf '%s' "$GEN_OUT" | head -c 200)"
    fi
fi

# ---- 5. GPU 가속 (옵션) ----------------------------------------------------
echo ""
echo "[5] GPU 가속 (선택적)"
if command -v nvidia-smi >/dev/null 2>&1; then
    if nvidia-smi --query-compute-apps=process_name --format=csv,noheader 2>/dev/null | grep -qi ollama; then
        ok "nvidia-smi 가 ollama 프로세스 인식 — GPU 가속 활성"
    else
        # 모델이 idle 일 땐 GPU 점유 안 함 — 경고만
        warn "nvidia-smi 에서 ollama 프로세스 미관찰 (idle 상태일 수 있음)"
    fi
else
    warn "nvidia-smi 없음 — CPU 환경"
fi

# ---- 요약 ------------------------------------------------------------------
echo ""
echo "────────────────────────────────────────────────"
echo "  통과: $(green "$PASS")   경고: $(yellow "$WARN")   실패: $(red "$FAIL")"
echo "────────────────────────────────────────────────"

if [ "$FAIL" -eq 0 ]; then
    echo "🎉 Ollama 정상. 다음 단계:"
    echo "   python -m backend.jobs.init_qdrant      # Qdrant 컬렉션 생성"
    exit 0
else
    echo "💥 ${FAIL} 개 항목 실패 — 'docker logs ${CONTAINER_NAME}' 로 원인 확인"
    exit 1
fi
