#!/usr/bin/env bash
# ============================================================================
# scripts/step1_check_base_env.sh
# ----------------------------------------------------------------------------
# step1_install_base_env.sh 로 셋업된 Python 베이스 환경이 정상 동작하는지 검증.
#
# 점검 항목:
#   1. .venv/ 디렉토리 존재
#   2. Python 3.11+
#   3. nvidia-smi 동작 (선택적 — 없으면 경고만)
#   4. torch import + CUDA 사용 가능 + GPU 인식
#   5. 핵심 의존 패키지 import (fastapi, sqlalchemy, qdrant_client,
#      sentence_transformers, openai, anthropic, httpx, streamlit, trafilatura)
#   6. env/dev.env 존재 (다음 단계 prerequisite)
#
# 사용:
#   bash scripts/step1_check_base_env.sh
#
# 종료 코드:
#   0  모든 필수 체크 통과
#   1  하나 이상의 필수 체크 실패
#
# CI 나 새 머신 재현 시 환경 셋업 직후 빠른 sanity check 용도.
# ============================================================================
set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV_DIR="${PROJECT_ROOT}/.venv"
VENV_PY="${VENV_DIR}/bin/python"
ENV_FILE="${PROJECT_ROOT}/env/dev.env"

# ---- 출력 헬퍼 -------------------------------------------------------------
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

# ---- 1. .venv 존재 ---------------------------------------------------------
echo "🔍 LinkMind 베이스 환경 점검"
echo "    프로젝트 루트: ${PROJECT_ROOT}"
echo ""
echo "[1] .venv 디렉토리"
if [ -d "$VENV_DIR" ] && [ -x "$VENV_PY" ]; then
    ok ".venv 존재 + Python 실행 파일 OK (${VENV_PY})"
else
    fail ".venv 가 없거나 Python 실행 파일이 없음 — 'bash scripts/step1_install_base_env.sh' 먼저 실행"
    # 이후 단계는 venv python 이 필수 → 더 진행해도 의미 없음
    echo ""
    echo "💥 환경 미설치 — 중단"
    exit 1
fi

# ---- 2. Python 버전 --------------------------------------------------------
echo ""
echo "[2] Python 버전 (3.11+)"
PY_VER="$("$VENV_PY" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")')"
if "$VENV_PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)'; then
    ok "Python ${PY_VER}"
else
    fail "Python ${PY_VER} — 3.11+ 필요"
fi

# ---- 3. nvidia-smi (선택적) ------------------------------------------------
echo ""
echo "[3] nvidia-smi (GPU 환경일 때만)"
if command -v nvidia-smi >/dev/null 2>&1; then
    DRIVER_VER="$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1 || true)"
    GPU_NAME="$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 || true)"
    if [ -n "$DRIVER_VER" ] && [ -n "$GPU_NAME" ]; then
        ok "${GPU_NAME} (driver ${DRIVER_VER})"
    else
        warn "nvidia-smi 는 있지만 GPU 조회 실패 — driver 상태 확인"
    fi
else
    warn "nvidia-smi 없음 — CPU 환경으로 간주 (--cpu 빌드인지 확인)"
fi

# ---- 4. torch + CUDA -------------------------------------------------------
echo ""
echo "[4] torch / CUDA"
TORCH_OUT="$("$VENV_PY" - <<'PYEOF' 2>&1
import sys
try:
    import torch
except ImportError as e:
    print(f"IMPORT_FAIL|{e}")
    sys.exit(2)
print(f"VERSION|{torch.__version__}")
print(f"CUDA_AVAILABLE|{torch.cuda.is_available()}")
if torch.cuda.is_available():
    print(f"CUDA_VERSION|{torch.version.cuda}")
    print(f"GPU_NAME|{torch.cuda.get_device_name(0)}")
PYEOF
)"
TORCH_RC=$?

if [ "$TORCH_RC" -ne 0 ]; then
    fail "torch import 실패: $(printf '%s' "$TORCH_OUT" | head -1)"
else
    TORCH_VERSION="$(printf '%s\n' "$TORCH_OUT" | awk -F'|' '/^VERSION\|/{print $2}')"
    CUDA_AVAIL="$(printf '%s\n'  "$TORCH_OUT" | awk -F'|' '/^CUDA_AVAILABLE\|/{print $2}')"
    CUDA_VER="$(printf '%s\n'    "$TORCH_OUT" | awk -F'|' '/^CUDA_VERSION\|/{print $2}')"
    GPU_NAME_T="$(printf '%s\n'  "$TORCH_OUT" | awk -F'|' '/^GPU_NAME\|/{print $2}')"

    ok "torch ${TORCH_VERSION}"
    if [ "$CUDA_AVAIL" = "True" ]; then
        ok "CUDA ${CUDA_VER} 사용 가능 — GPU: ${GPU_NAME_T}"
    else
        warn "torch 는 OK 지만 CUDA 미인식 — CPU only 환경이면 정상, GPU 환경이면 driver/wheel 확인"
    fi
fi

# ---- 5. 핵심 패키지 import -------------------------------------------------
echo ""
echo "[5] 핵심 의존 패키지 import"
PKG_OUT="$("$VENV_PY" - <<'PYEOF'
import importlib

# (import_name, distribution_name) 매핑.
# import 명과 pip 패키지명이 다른 경우가 있어 둘 다 표시.
pkgs = [
    ("fastapi",                "fastapi"),
    ("sqlalchemy",             "sqlalchemy"),
    ("qdrant_client",          "qdrant-client"),
    ("sentence_transformers",  "sentence-transformers"),
    ("openai",                 "openai"),
    ("anthropic",              "anthropic"),
    ("httpx",                  "httpx"),
    ("streamlit",              "streamlit"),
    ("trafilatura",            "trafilatura"),
    ("pydantic_settings",      "pydantic-settings"),
    ("asyncpg",                "asyncpg"),
]

failures = []
for imp, dist in pkgs:
    try:
        m = importlib.import_module(imp)
        ver = getattr(m, "__version__", None)
        if ver is None:
            # __version__ 이 없는 패키지는 importlib.metadata 로 보조 조회
            try:
                from importlib.metadata import version as _v
                ver = _v(dist)
            except Exception:
                ver = "?"
        print(f"OK|{imp}|{ver}")
    except ImportError as e:
        print(f"FAIL|{imp}|{e}")
        failures.append(imp)

import sys
sys.exit(1 if failures else 0)
PYEOF
)"
PKG_RC=$?

while IFS='|' read -r status name ver; do
    [ -z "$status" ] && continue
    if [ "$status" = "OK" ]; then
        ok "$(printf '%-24s %s' "$name" "$ver")"
    else
        fail "${name} import 실패: ${ver}"
    fi
done <<< "$PKG_OUT"

if [ "$PKG_RC" -ne 0 ]; then
    : # 위 루프에서 이미 fail() 카운트 됨
fi

# ---- 6. env/dev.env 존재 ---------------------------------------------------
echo ""
echo "[6] env/dev.env (다음 단계 docker compose prerequisite)"
if [ -f "$ENV_FILE" ]; then
    ok "${ENV_FILE} 존재"
else
    warn "${ENV_FILE} 없음 — 'cp env/dev.env.example env/dev.env' 후 비밀값 채우기"
fi

# ---- 요약 ------------------------------------------------------------------
echo ""
echo "────────────────────────────────────────────────"
echo "  통과: $(green "$PASS")   경고: $(yellow "$WARN")   실패: $(red "$FAIL")"
echo "────────────────────────────────────────────────"

if [ "$FAIL" -eq 0 ]; then
    echo "🎉 베이스 환경 정상. 다음 단계로 진행 가능:"
    echo "   bash scripts/step2_2_setup_infra.sh        # docker compose 인프라 기동"
    exit 0
else
    echo "💥 ${FAIL} 개 항목 실패 — 위 메시지 확인 후 재셋업 (bash scripts/step1_install_base_env.sh)"
    exit 1
fi
