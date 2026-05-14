#!/usr/bin/env bash
# ============================================================================
# scripts/step2_1_check_docker.sh
# ----------------------------------------------------------------------------
# step2_1_install_docker.sh 로 설치된 docker / nvidia runtime 이 정상 동작하는지
# 빠른 sanity check. 컨테이너는 띄우지 않는다 (그건 step2_2).
#
# 점검 항목:
#   1. docker 명령 존재 + 버전
#   2. docker compose v2 plugin 존재 + 버전
#   3. docker info 가능 (= 현재 셸에 docker 그룹 권한 적용됨)
#   4. nvidia runtime 등록됨 (선택 — GPU 환경에서만)
#   5. (간단) hello-world 컨테이너 실행 가능
#
# 사용:
#   bash scripts/step2_1_check_docker.sh
#   bash scripts/step2_1_check_docker.sh --no-nvidia   # GPU 검증 skip
#   bash scripts/step2_1_check_docker.sh --no-pull     # hello-world 검증 skip (오프라인)
# ============================================================================
set -uo pipefail

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

USE_NVIDIA=1
DO_PULL=1
for arg in "$@"; do
    case "$arg" in
        --no-nvidia) USE_NVIDIA=0 ;;
        --no-pull)   DO_PULL=0 ;;
        -h|--help)   sed -n '1,22p' "$0"; exit 0 ;;
        *)           echo "❌ 알 수 없는 인자: $arg"; exit 2 ;;
    esac
done

echo "🔍 Docker / NVIDIA Container Toolkit 점검"
echo ""

# ---- 1. docker 명령 --------------------------------------------------------
echo "[1] docker 명령"
if command -v docker >/dev/null 2>&1; then
    ok "docker $(docker --version | awk '{print $3}' | tr -d ',')"
else
    fail "docker 명령 없음 — 'bash scripts/step2_1_install_docker.sh' 먼저"
    echo ""; echo "💥 docker 없음 — 중단"; exit 1
fi

# ---- 2. docker compose v2 --------------------------------------------------
echo ""
echo "[2] docker compose v2 plugin"
if docker compose version >/dev/null 2>&1; then
    ok "docker compose $(docker compose version --short)"
else
    fail "docker compose v2 plugin 없음 — 재설치 필요"
fi

# ---- 3. docker info (현재 셸 그룹 권한) ------------------------------------
echo ""
echo "[3] 현재 셸 docker 데몬 접근"
if docker info >/dev/null 2>&1; then
    SERVER_VER="$(docker info --format '{{.ServerVersion}}' 2>/dev/null || echo unknown)"
    ok "docker info 응답 OK (server ${SERVER_VER})"
else
    fail "docker info 실패 — 현재 셸에 'docker' 그룹 미적용. 재로그인 또는 'newgrp docker' 필요"
    echo ""; echo "💥 그룹 권한 미적용 — 중단"; exit 1
fi

# ---- 4. NVIDIA runtime -----------------------------------------------------
echo ""
echo "[4] NVIDIA Container Runtime"
if [ "$USE_NVIDIA" -eq 0 ]; then
    warn "--no-nvidia — GPU 검증 skip"
elif ! command -v nvidia-smi >/dev/null 2>&1; then
    warn "nvidia-smi 없음 — CPU 환경으로 간주"
else
    RUNTIMES="$(docker info --format '{{range .Runtimes}}{{.Name}} {{end}}' 2>/dev/null || true)"
    if echo "$RUNTIMES" | grep -qw nvidia; then
        ok "docker 에 nvidia runtime 등록됨"
    else
        fail "docker 에 nvidia runtime 미등록 — 'sudo nvidia-ctk runtime configure --runtime=docker' 후 docker 재시작"
    fi
fi

# ---- 5. hello-world ---------------------------------------------------------
echo ""
echo "[5] hello-world 컨테이너 (이미지 pull + 실행)"
if [ "$DO_PULL" -eq 0 ]; then
    warn "--no-pull — hello-world 검증 skip"
else
    if docker run --rm hello-world >/dev/null 2>&1; then
        ok "hello-world 실행 OK — docker 풀체인 정상"
    else
        fail "hello-world 실행 실패 — 'docker run --rm hello-world' 로 직접 확인"
    fi
fi

# ---- 요약 ------------------------------------------------------------------
echo ""
echo "────────────────────────────────────────────────"
echo "  통과: $(green "$PASS")   경고: $(yellow "$WARN")   실패: $(red "$FAIL")"
echo "────────────────────────────────────────────────"

if [ "$FAIL" -eq 0 ]; then
    echo "🎉 docker 환경 정상. 다음 단계:"
    echo "   bash scripts/step2_2_setup_infra.sh      # Postgres + Qdrant + Ollama + OpenWebUI 기동"
    exit 0
else
    echo "💥 ${FAIL} 개 항목 실패 — 위 메시지 확인"
    exit 1
fi
