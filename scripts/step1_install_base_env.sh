#!/usr/bin/env bash
# ============================================================================
# scripts/step1_install_base_env.sh
# ----------------------------------------------------------------------------
# LinkMind 의 Python 베이스 환경(venv + torch + requirements) 을 한 번에 셋업.
#
# 핵심 포인트:
#   - venv 사용 (conda 아님 — CLAUDE.md §4 정책)
#   - torch CUDA wheel 을 **먼저** 설치, 그 다음 requirements.txt
#     → PyPI 의 CPU torch 를 받았다가 폐기하는 낭비 방지
#   - 기본 CUDA 버전: cu124 (RTX 4090 권장)
#
# 사용:
#   bash scripts/step1_install_base_env.sh                    # 기본: cu124 + requirements
#   bash scripts/step1_install_base_env.sh --recreate         # 기존 .venv 삭제 후 처음부터
#   bash scripts/step1_install_base_env.sh --cpu              # CPU torch (GPU 없는 환경)
#   bash scripts/step1_install_base_env.sh --cuda-version=126 # cu126 wheel 사용
#
# 종료 후 활성화 (이 스크립트는 부모 쉘의 환경을 못 바꿈):
#   source .venv/bin/activate
# ============================================================================
set -euo pipefail

# ----------------------------------------------------------------------------
# 인자 파싱
# ----------------------------------------------------------------------------
RECREATE=0
USE_CPU=0
CUDA_VER="124"

for arg in "$@"; do
    case "$arg" in
        --recreate|--from-scratch) RECREATE=1 ;;
        --cpu)                     USE_CPU=1 ;;
        --cuda-version=*)          CUDA_VER="${arg#--cuda-version=}" ;;
        -h|--help)
            sed -n '1,22p' "$0"
            exit 0
            ;;
        *)
            echo "❌ 알 수 없는 인자: $arg"
            sed -n '1,22p' "$0"
            exit 2
            ;;
    esac
done

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV_DIR="${PROJECT_ROOT}/.venv"
REQUIREMENTS="${PROJECT_ROOT}/requirements.txt"

echo "🐍 LinkMind 베이스 환경 셋업 시작"
echo "    프로젝트 루트: ${PROJECT_ROOT}"
echo "    venv 경로:    ${VENV_DIR}"
echo "    requirements: ${REQUIREMENTS}"
if [ "$USE_CPU" -eq 1 ]; then
    echo "    torch 빌드:   CPU only (--cpu)"
else
    echo "    torch 빌드:   CUDA cu${CUDA_VER}"
fi
echo ""

# ----------------------------------------------------------------------------
# 1. Python 3.11+ 확인
# ----------------------------------------------------------------------------
# 3.11 이 있으면 우선 사용, 없으면 python3.
PY_BIN=""
for cand in python3.12 python3.11 python3; do
    if command -v "$cand" >/dev/null 2>&1; then
        PY_BIN="$cand"
        break
    fi
done
if [ -z "$PY_BIN" ]; then
    echo "❌ python3 가 없습니다."
    exit 1
fi
PY_VER="$("$PY_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
echo "✅ Python 인터프리터: ${PY_BIN} (버전 ${PY_VER})"
if ! "$PY_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)'; then
    cat <<EOF
❌ Python 3.11+ 가 필요합니다. 현재: ${PY_VER}

    Ubuntu 에서 설치 예 (deadsnakes PPA):
      sudo add-apt-repository -y ppa:deadsnakes/ppa
      sudo apt update
      sudo apt install -y python3.11 python3.11-venv python3.11-dev

EOF
    exit 1
fi

# ----------------------------------------------------------------------------
# 2. venv 생성 또는 재사용
# ----------------------------------------------------------------------------
if [ "$RECREATE" -eq 1 ] && [ -d "$VENV_DIR" ]; then
    echo "🗑️  기존 .venv 제거 (--recreate)"
    rm -rf "$VENV_DIR"
fi

if [ ! -d "$VENV_DIR" ]; then
    echo "📦 venv 생성: ${VENV_DIR}"
    "$PY_BIN" -m venv "$VENV_DIR"
else
    echo "✅ 기존 venv 재사용 (--recreate 로 새로 만들기 가능)"
fi

VENV_PIP="${VENV_DIR}/bin/pip"
VENV_PY="${VENV_DIR}/bin/python"

# ----------------------------------------------------------------------------
# 3. pip / wheel / setuptools 업그레이드
# ----------------------------------------------------------------------------
echo ""
echo "⬆️  pip / wheel / setuptools 업그레이드"
"$VENV_PIP" install --upgrade pip wheel setuptools >/dev/null

# ----------------------------------------------------------------------------
# 4. torch 먼저 설치 (CPU 빌드를 받았다가 폐기하는 낭비 방지)
# ----------------------------------------------------------------------------
echo ""
if [ "$USE_CPU" -eq 1 ]; then
    echo "🧠 torch CPU 빌드 설치 (PyPI default)"
    "$VENV_PIP" install --force-reinstall torch
else
    INDEX_URL="https://download.pytorch.org/whl/cu${CUDA_VER}"
    echo "🚀 torch CUDA wheel 설치 (${INDEX_URL})"
    "$VENV_PIP" install --force-reinstall --index-url "$INDEX_URL" torch
fi

# ----------------------------------------------------------------------------
# 5. requirements.txt 의 나머지 (torch 는 이미 만족 → 자동 skip)
# ----------------------------------------------------------------------------
if [ -f "$REQUIREMENTS" ]; then
    echo ""
    echo "📜 requirements.txt 설치"
    "$VENV_PIP" install -r "$REQUIREMENTS"
else
    echo "⚠️  requirements.txt 가 없음: ${REQUIREMENTS}"
fi

# ----------------------------------------------------------------------------
# 6. 검증
# ----------------------------------------------------------------------------
echo ""
echo "🧪 설치 검증"
"$VENV_PY" - <<'PYEOF'
import sys
print(f"  Python:     {sys.version.split()[0]}  ({sys.executable})")

try:
    import torch
    print(f"  torch:      {torch.__version__}")
    cuda_ok = torch.cuda.is_available()
    print(f"  CUDA 사용:  {cuda_ok}")
    if cuda_ok:
        print(f"  CUDA ver:   {torch.version.cuda}")
        print(f"  GPU:        {torch.cuda.get_device_name(0)}")
    else:
        print("    ⚠️  torch 는 설치됐지만 GPU 미인식. nvidia 드라이버/모듈 상태 확인:")
        print("       nvidia-smi  /  lsmod | grep nvidia  /  sudo modprobe nvidia")
except ImportError as e:
    print(f"  ❌ torch import 실패: {e}")

for mod in ("fastapi", "sqlalchemy", "qdrant_client", "sentence_transformers",
            "openai", "anthropic", "httpx", "streamlit", "trafilatura"):
    try:
        m = __import__(mod)
        v = getattr(m, "__version__", "?")
        print(f"  {mod:24s} {v}")
    except ImportError:
        print(f"  ❌ {mod} import 실패")
PYEOF

echo ""
echo "🎉 베이스 환경 셋업 완료"
echo ""
echo "다음 단계:"
echo "  source ${VENV_DIR}/bin/activate                # 현재 셸에 활성화"
echo "  bash scripts/step1_check_base_env.sh           # 설치 결과 sanity check"
echo "  bash scripts/step2_2_setup_infra.sh              # docker compose 인프라"
echo "  bash scripts/step3_setup_ollama.sh             # Ollama 컨테이너 + 모델 pull"
echo "  python scripts/step4_init_qdrant.py            # bge-m3 1.4GB 첫 다운로드"
echo "  uvicorn backend.main:app --reload              # 백엔드"
echo "  streamlit run frontend/app.py                  # 프론트"
