#!/usr/bin/env bash
# ============================================================================
# scripts/step2_2_check_infra.sh
# ----------------------------------------------------------------------------
# step2_2_setup_infra.sh 로 띄운 인프라 컨테이너들이 정상 동작하는지 검증.
#
# 점검 항목:
#   1. docker / docker compose 가용
#   2. env/dev.env 존재 + 핵심 변수 채워짐 (POSTGRES_*, QDRANT_*, OLLAMA_*)
#   3. Postgres 컨테이너 healthy + 호스트 포트로 연결 가능
#   4. Qdrant 컨테이너 healthy + /readyz endpoint 응답
#   5. Ollama 컨테이너 healthy + /api/tags endpoint 응답
#   6. OpenWebUI 컨테이너 running (healthcheck 미정의 → 기동만 확인)
#
# 사용:
#   bash scripts/step2_2_check_infra.sh
#
# 종료 코드:
#   0  모든 필수 체크 통과
#   1  하나 이상의 필수 체크 실패
# ============================================================================
set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${PROJECT_ROOT}/env/dev.env"
COMPOSE_FILE="${PROJECT_ROOT}/compose/docker-compose.dev.yml"

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

# ---- env 변수 로드 (호스트 포트 확인용) ------------------------------------
# .env 형식이므로 source 로 읽음. 값에 공백이 있으면 따옴표 필요하지만 일반적으로 단순.
if [ -f "$ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    . "$ENV_FILE"
    set +a
fi

POSTGRES_PORT="${POSTGRES_PORT:-5432}"
QDRANT_HTTP_PORT="${QDRANT_HTTP_PORT:-6333}"
OLLAMA_PORT="${OLLAMA_PORT:-11434}"
OPENWEBUI_PORT="${OPENWEBUI_PORT:-3000}"

echo "🔍 LinkMind 인프라 점검"
echo "    compose:  ${COMPOSE_FILE}"
echo "    env-file: ${ENV_FILE}"
echo ""

# ---- 1. docker / docker compose --------------------------------------------
echo "[1] docker / docker compose"
if command -v docker >/dev/null 2>&1; then
    ok "docker $(docker --version | awk '{print $3}' | tr -d ',')"
else
    fail "docker 없음 — https://docs.docker.com/engine/install/"
    echo ""; echo "💥 docker 없음 — 중단"; exit 1
fi
if docker compose version >/dev/null 2>&1; then
    ok "docker compose $(docker compose version --short)"
else
    fail "docker compose v2 plugin 없음"
    echo ""; echo "💥 compose 없음 — 중단"; exit 1
fi

# ---- 2. env 파일 + 핵심 변수 -----------------------------------------------
echo ""
echo "[2] env/dev.env 필수 변수"
if [ ! -f "$ENV_FILE" ]; then
    fail "${ENV_FILE} 없음 — cp env/dev.env.example env/dev.env"
    echo ""; echo "💥 env 없음 — 중단"; exit 1
else
    ok "env/dev.env 존재"
fi

# placeholder 가 그대로 있는지 확인 (간단 휴리스틱)
if grep -q '^POSTGRES_PASSWORD=changeme' "$ENV_FILE"; then
    warn "POSTGRES_PASSWORD 가 changeme_* placeholder 그대로 — 실제 비밀번호로 교체 권장"
else
    ok "POSTGRES_PASSWORD 변경됨"
fi

# ---- 3. 컨테이너 상태 + healthcheck ----------------------------------------
inspect_container() {
    # $1 = service name (compose)
    # $2 = container name (실제 컨테이너명, fallback)
    local svc="$1"
    local cname="$2"
    local cid
    cid="$(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps -q "$svc" 2>/dev/null || true)"
    if [ -z "$cid" ]; then
        # compose 메타데이터 없을 수 있음 → 컨테이너명으로 직접 조회
        cid="$(docker ps -aq --filter "name=^${cname}$" 2>/dev/null || true)"
    fi
    echo "$cid"
}

echo ""
echo "[3] Postgres"
PG_CID="$(inspect_container postgres linkmind-postgres)"
if [ -z "$PG_CID" ]; then
    fail "linkmind-postgres 컨테이너 없음 — 'bash scripts/step2_2_setup_infra.sh' 먼저"
else
    PG_HEALTH="$(docker inspect -f '{{.State.Health.Status}}' "$PG_CID" 2>/dev/null || echo unknown)"
    PG_RUN="$(docker inspect -f '{{.State.Running}}' "$PG_CID" 2>/dev/null || echo unknown)"
    if [ "$PG_HEALTH" = "healthy" ]; then
        ok "컨테이너 healthy (running=${PG_RUN})"
    else
        fail "컨테이너 상태: health=${PG_HEALTH} running=${PG_RUN}"
    fi
    # 호스트 포트로 TCP 연결 가능 여부
    if (echo > "/dev/tcp/127.0.0.1/${POSTGRES_PORT}") >/dev/null 2>&1; then
        ok "호스트 포트 ${POSTGRES_PORT} 응답"
    else
        fail "호스트 포트 ${POSTGRES_PORT} 연결 실패"
    fi
fi

echo ""
echo "[4] Qdrant"
QD_CID="$(inspect_container qdrant linkmind-qdrant)"
if [ -z "$QD_CID" ]; then
    fail "linkmind-qdrant 컨테이너 없음"
else
    QD_HEALTH="$(docker inspect -f '{{.State.Health.Status}}' "$QD_CID" 2>/dev/null || echo unknown)"
    if [ "$QD_HEALTH" = "healthy" ]; then
        ok "컨테이너 healthy"
    else
        fail "컨테이너 상태: health=${QD_HEALTH}"
    fi
    if command -v curl >/dev/null 2>&1; then
        if curl -fsS "http://127.0.0.1:${QDRANT_HTTP_PORT}/readyz" >/dev/null 2>&1; then
            ok "/readyz 200 OK (port ${QDRANT_HTTP_PORT})"
        else
            fail "/readyz 응답 실패 (port ${QDRANT_HTTP_PORT})"
        fi
    else
        warn "curl 없음 — /readyz 검증 생략"
    fi
fi

echo ""
echo "[5] Ollama"
OL_CID="$(inspect_container ollama linkmind-ollama)"
if [ -z "$OL_CID" ]; then
    fail "linkmind-ollama 컨테이너 없음"
else
    OL_HEALTH="$(docker inspect -f '{{.State.Health.Status}}' "$OL_CID" 2>/dev/null || echo unknown)"
    if [ "$OL_HEALTH" = "healthy" ]; then
        ok "컨테이너 healthy"
    else
        fail "컨테이너 상태: health=${OL_HEALTH}"
    fi
    if command -v curl >/dev/null 2>&1; then
        if curl -fsS "http://127.0.0.1:${OLLAMA_PORT}/api/tags" >/dev/null 2>&1; then
            ok "/api/tags 200 OK (port ${OLLAMA_PORT})"
        else
            fail "/api/tags 응답 실패 (port ${OLLAMA_PORT})"
        fi
    else
        warn "curl 없음 — /api/tags 검증 생략"
    fi
fi

echo ""
echo "[6] OpenWebUI (healthcheck 미정의 → running 만 확인)"
OW_CID="$(inspect_container openwebui linkmind-openwebui)"
if [ -z "$OW_CID" ]; then
    fail "linkmind-openwebui 컨테이너 없음"
else
    OW_RUN="$(docker inspect -f '{{.State.Running}}' "$OW_CID" 2>/dev/null || echo unknown)"
    if [ "$OW_RUN" = "true" ]; then
        ok "컨테이너 running (port ${OPENWEBUI_PORT})"
    else
        fail "컨테이너 상태: running=${OW_RUN}"
    fi
fi

# ---- 7~8. vLLM 인스턴스 (선택, --profile vllm 활성화 시) -------------------
# 컨테이너 없으면 info 로 skip (필수 아님). starting 상태 (모델 다운로드 중) 는 warn.

check_vllm_instance() {
    # $1 = label (사람이 보기 좋은 이름), $2 = compose service, $3 = container name,
    # $4 = 호스트 포트, $5 = 기대 모델 이름 substring (예: "Qwen2.5-7B" 또는 "bge-m3")
    local label="$1" svc="$2" cname="$3" port="$4" expect_model="$5"
    local cid health run
    cid="$(inspect_container "$svc" "$cname")"
    if [ -z "$cid" ]; then
        printf '  %s  %s — 미가동 (선택 — `docker compose --profile vllm up -d` 로 활성화)\n' \
            "$(yellow 'ℹ️ ')" "$label"
        return
    fi
    health="$(docker inspect -f '{{.State.Health.Status}}' "$cid" 2>/dev/null || echo unknown)"
    run="$(docker inspect -f '{{.State.Running}}' "$cid" 2>/dev/null || echo unknown)"
    case "$health" in
        healthy)
            ok "$label 컨테이너 healthy (running=${run})"
            ;;
        starting)
            warn "$label 컨테이너 starting — 첫 시작이면 모델 다운로드 중일 수 있음 (5-30분)"
            ;;
        *)
            fail "$label 상태: health=${health} running=${run}"
            return
            ;;
    esac
    # /v1/models endpoint 응답 + 기대 모델 이름 매칭
    if command -v curl >/dev/null 2>&1; then
        local resp
        resp="$(curl -fsS -m 5 "http://127.0.0.1:${port}/v1/models" 2>/dev/null || true)"
        if [ -z "$resp" ]; then
            warn "$label /v1/models 응답 없음 (port ${port}) — 모델 로드 중일 수 있음"
        elif echo "$resp" | grep -q "$expect_model"; then
            ok "$label /v1/models 응답에 '${expect_model}' 확인"
        else
            warn "$label /v1/models 응답에 '${expect_model}' 없음 — VLLM_*_MODEL env 확인"
        fi
    fi
}

echo ""
echo "[7] vLLM (LLM, 선택)"
check_vllm_instance "vLLM-llm" vllm linkmind-vllm 8001 "${VLLM_MODEL:-Qwen2.5-7B}"

echo ""
echo "[8] vLLM-embed (선택, CLAUDE.md §13 D13)"
check_vllm_instance "vLLM-embed" vllm-embed linkmind-vllm-embed 8002 "${VLLM_EMBED_MODEL:-bge-m3}"

# ---- 요약 ------------------------------------------------------------------
echo ""
echo "────────────────────────────────────────────────"
echo "  통과: $(green "$PASS")   경고: $(yellow "$WARN")   실패: $(red "$FAIL")"
echo "────────────────────────────────────────────────"

if [ "$FAIL" -eq 0 ]; then
    echo "🎉 인프라 정상. 다음 단계:"
    echo "   bash scripts/step3_setup_ollama.sh       # qwen2.5:7b pull + 검증"
    echo "   python -m backend.jobs.init_qdrant      # Qdrant 컬렉션 생성"
    exit 0
else
    echo "💥 ${FAIL} 개 항목 실패 — 'docker compose logs <서비스>' 로 원인 확인"
    exit 1
fi
