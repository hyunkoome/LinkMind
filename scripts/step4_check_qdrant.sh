#!/usr/bin/env bash
# ============================================================================
# scripts/step4_check_qdrant.sh
# ----------------------------------------------------------------------------
# step4_init_qdrant.py 로 만든 Qdrant 컬렉션이 정상 동작하는지 검증.
#
# 점검 항목:
#   1. Qdrant API (/readyz) 응답
#   2. env/dev.env 의 QDRANT_COLLECTION 이 존재
#   3. 컬렉션 vector size 가 EMBEDDING_DIM 과 일치
#   4. (간단) 컬렉션 정보(/collections/<name>) JSON 파싱 가능
#
# 사용:
#   bash scripts/step4_check_qdrant.sh
#
# 종료 코드:
#   0  모든 필수 체크 통과
#   1  하나 이상의 필수 체크 실패
# ============================================================================
set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${PROJECT_ROOT}/env/dev.env"

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
QDRANT_HTTP_PORT="${QDRANT_HTTP_PORT:-6333}"
QDRANT_COLLECTION="${QDRANT_COLLECTION:-linkmind_items}"
EMBEDDING_DIM="${EMBEDDING_DIM:-1024}"

echo "🔍 Qdrant 컬렉션 점검"
echo "    컬렉션:    ${QDRANT_COLLECTION}"
echo "    예상 dim:  ${EMBEDDING_DIM}"
echo ""

# ---- 1. /readyz -----------------------------------------------------------
echo "[1] Qdrant API /readyz (port ${QDRANT_HTTP_PORT})"
if ! command -v curl >/dev/null 2>&1; then
    fail "curl 없음 — 설치 필요"
    echo ""; echo "💥 curl 없음 — 중단"; exit 1
fi
if curl -fsS "http://127.0.0.1:${QDRANT_HTTP_PORT}/readyz" >/dev/null 2>&1; then
    ok "/readyz 200 OK"
else
    fail "/readyz 응답 실패 — 'bash scripts/step2_setup_infra.sh' 먼저"
    echo ""; echo "💥 Qdrant 미응답 — 중단"; exit 1
fi

# ---- 2. 컬렉션 존재 + 3. dim 일치 -----------------------------------------
echo ""
echo "[2-3] 컬렉션 정보 조회"
COLL_JSON="$(curl -fsS "http://127.0.0.1:${QDRANT_HTTP_PORT}/collections/${QDRANT_COLLECTION}" 2>/dev/null || true)"

if [ -z "$COLL_JSON" ] || ! printf '%s' "$COLL_JSON" | grep -q '"status":"ok"'; then
    fail "컬렉션 '${QDRANT_COLLECTION}' 조회 실패 — 'python scripts/step4_init_qdrant.py' 먼저"
    echo ""
    echo "💥 컬렉션 없음 — 중단"
    exit 1
fi
ok "컬렉션 '${QDRANT_COLLECTION}' 존재"

# vector size 파싱 — JSON 은 stdin 으로 넘김 (따옴표/escape 안전)
ACTUAL_DIM="$(printf '%s' "$COLL_JSON" | python3 - <<'PYEOF' 2>/dev/null
import json, sys
try:
    j = json.load(sys.stdin)
    # 단일 vector / named vectors 두 경우 모두 처리
    cfg = j["result"]["config"]["params"]["vectors"]
    if isinstance(cfg, dict) and "size" in cfg:
        print(cfg["size"])
    elif isinstance(cfg, dict):
        first = next(iter(cfg.values()))
        print(first["size"])
    else:
        sys.exit(1)
except Exception:
    sys.exit(1)
PYEOF
)" || ACTUAL_DIM=""

if [ -z "$ACTUAL_DIM" ]; then
    warn "vector size 파싱 실패 — 응답: $(printf '%s' "$COLL_JSON" | head -c 200)"
elif [ "$ACTUAL_DIM" = "$EMBEDDING_DIM" ]; then
    ok "vector size ${ACTUAL_DIM} == EMBEDDING_DIM (${EMBEDDING_DIM})"
else
    fail "vector size 불일치: 컬렉션=${ACTUAL_DIM}, env=${EMBEDDING_DIM} — recreate 필요"
fi

# ---- 4. 컬렉션 추가 메타 ---------------------------------------------------
echo ""
echo "[4] 컬렉션 추가 정보"
POINTS_COUNT="$(printf '%s' "$COLL_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['result'].get('points_count', '?'))" 2>/dev/null || echo "?")"
ok "points_count = ${POINTS_COUNT}"

# ---- 요약 ------------------------------------------------------------------
echo ""
echo "────────────────────────────────────────────────"
echo "  통과: $(green "$PASS")   경고: $(yellow "$WARN")   실패: $(red "$FAIL")"
echo "────────────────────────────────────────────────"

if [ "$FAIL" -eq 0 ]; then
    echo "🎉 Qdrant 준비 완료. 다음 단계:"
    echo "   uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000"
    echo "   streamlit run frontend/app.py"
    exit 0
else
    echo "💥 ${FAIL} 개 항목 실패 — 컬렉션 dim 불일치면 재생성 필요"
    exit 1
fi
