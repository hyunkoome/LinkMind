#!/usr/bin/env bash
# ============================================================================
# scripts/step2_1_install_docker.sh
# ----------------------------------------------------------------------------
# Ubuntu/Debian 에 Docker Engine + Docker Compose plugin + nvidia-container-toolkit
# 설치. LinkMind step2_2 (인프라 컨테이너) 진행 전 사전 요구사항.
#
# 멱등성: 이미 설치된 부분은 skip.
# sudo 권한 필요 — 비대화형 환경에서는 sudo NOPASSWD 설정되어 있어야 함.
#
# 사용:
#   bash scripts/step2_1_install_docker.sh                # docker + nvidia toolkit
#   bash scripts/step2_1_install_docker.sh --no-nvidia    # CPU 환경 (toolkit skip)
#
# 끝나면:
#   - docker / docker compose 명령 사용 가능
#   - 현재 사용자가 docker 그룹에 포함 → 'newgrp docker' 또는 로그아웃/재로그인 필요
#   - GPU 환경에선 'docker run --rm --gpus all ...' 동작
#
# 다음 단계:
#   1) 새 셸 열기:        exec su -l "$USER"     # 또는 SSH 재로그인
#      또는 임시 격상:    newgrp docker
#   2) 검증:              bash scripts/step2_1_check_docker.sh
#   3) 인프라 기동:       bash scripts/step2_2_setup_infra.sh
# ============================================================================
set -euo pipefail

USE_NVIDIA=1
for arg in "$@"; do
    case "$arg" in
        --no-nvidia) USE_NVIDIA=0 ;;
        -h|--help)
            sed -n '1,26p' "$0"
            exit 0
            ;;
        *)
            echo "❌ 알 수 없는 인자: $arg"
            exit 2
            ;;
    esac
done

# ---- 0. OS 확인 ------------------------------------------------------------
if [ -r /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    DISTRO_ID="${ID:-unknown}"
    DISTRO_CODENAME="${VERSION_CODENAME:-}"
else
    echo "❌ /etc/os-release 가 없습니다 — Ubuntu/Debian 외 환경은 매뉴얼 설치 권장"
    exit 1
fi

if [ "$DISTRO_ID" != "ubuntu" ] && [ "$DISTRO_ID" != "debian" ]; then
    echo "⚠️  ${DISTRO_ID} 감지 — Ubuntu/Debian 외 환경. 매뉴얼 설치 권장"
    echo "   https://docs.docker.com/engine/install/"
    exit 1
fi

echo "🐳 Docker + NVIDIA Container Toolkit 설치 (${DISTRO_ID} ${DISTRO_CODENAME})"
echo "    GPU 지원:  $([ $USE_NVIDIA -eq 1 ] && echo 'on' || echo 'off')"
echo ""

# ---- 1. Docker Engine ------------------------------------------------------
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    echo "✅ docker + compose 이미 설치됨 ($(docker --version))"
else
    echo "📦 Docker 공식 apt repo 등록 + docker-ce 설치"
    # https://docs.docker.com/engine/install/ubuntu/ 의 절차 그대로
    sudo apt-get update
    sudo apt-get install -y ca-certificates curl gnupg
    sudo install -m 0755 -d /etc/apt/keyrings
    sudo curl -fsSL "https://download.docker.com/linux/${DISTRO_ID}/gpg" \
        -o /etc/apt/keyrings/docker.asc
    sudo chmod a+r /etc/apt/keyrings/docker.asc

    ARCH="$(dpkg --print-architecture)"
    echo "deb [arch=${ARCH} signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/${DISTRO_ID} ${DISTRO_CODENAME} stable" \
        | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null

    sudo apt-get update
    sudo apt-get install -y \
        docker-ce docker-ce-cli containerd.io \
        docker-buildx-plugin docker-compose-plugin

    echo "✅ Docker Engine 설치 완료 ($(docker --version))"
fi

# ---- 2. 현재 사용자를 docker 그룹에 추가 -----------------------------------
if id -nG "$USER" | grep -qw docker; then
    echo "✅ ${USER} 이미 docker 그룹 소속"
else
    echo "👤 ${USER} 를 docker 그룹에 추가"
    sudo usermod -aG docker "$USER"
    NEED_RELOGIN=1
fi

# ---- 3. nvidia-container-toolkit -------------------------------------------
if [ "$USE_NVIDIA" -eq 0 ]; then
    echo "ℹ️  --no-nvidia — nvidia-container-toolkit skip"
elif ! command -v nvidia-smi >/dev/null 2>&1; then
    echo "ℹ️  nvidia-smi 없음 — CPU 환경으로 간주, toolkit skip"
elif command -v nvidia-ctk >/dev/null 2>&1; then
    echo "✅ nvidia-container-toolkit 이미 설치됨"
    # 이미 설치돼있어도 docker runtime 설정은 한 번 더 보장 (idempotent)
    sudo nvidia-ctk runtime configure --runtime=docker >/dev/null
else
    echo "📦 nvidia-container-toolkit 설치"
    curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
        | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
    curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
        | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
        | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list >/dev/null

    sudo apt-get update
    sudo apt-get install -y nvidia-container-toolkit

    sudo nvidia-ctk runtime configure --runtime=docker
    sudo systemctl restart docker
    echo "✅ nvidia-container-toolkit 설치 + docker runtime 설정 완료"
fi

# ---- 4. 즉시 검증 ----------------------------------------------------------
# docker 그룹이 현재 셸엔 적용 안 됐을 수 있으니 NEED_RELOGIN 일 땐 sudo 사용.
DOCKER_RUN=(docker)
[ "${NEED_RELOGIN:-0}" -eq 1 ] && DOCKER_RUN=(sudo docker)

echo ""
echo "🧪 hello-world 컨테이너로 docker 검증"
"${DOCKER_RUN[@]}" run --rm hello-world | tail -5 \
    || echo "⚠️  hello-world 실행 실패 — 'docker run --rm hello-world' 로 직접 확인"

if [ "$USE_NVIDIA" -eq 1 ] \
        && command -v nvidia-smi >/dev/null 2>&1 \
        && command -v nvidia-ctk >/dev/null 2>&1; then
    echo ""
    echo "🧪 GPU passthrough 검증 (nvidia/cuda 컨테이너로 nvidia-smi)"
    "${DOCKER_RUN[@]}" run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi \
        || echo "⚠️  GPU passthrough 검증 실패 — nvidia-ctk 설정 확인"
fi

# ---- 5. 마무리 안내 --------------------------------------------------------
echo ""
echo "🎉 설치 단계 끝."
echo ""

if [ "${NEED_RELOGIN:-0}" -eq 1 ]; then
    cat <<'EOF'
⚠️  docker 그룹 권한이 새로 부여됨 — 현재 셸엔 아직 미적용.

   다음 중 하나로 적용한 뒤 step2_2 진행:

   1) 새 셸 열기 (가장 깔끔):
        exec su -l "$USER"
      또는 터미널 / SSH 재로그인.

   2) 현재 셸만 임시로 그룹 격상:
        newgrp docker

   이후:
        bash scripts/step2_1_check_docker.sh     # 설치 결과 검증
        bash scripts/step2_2_setup_infra.sh      # Postgres + Qdrant + Ollama + OpenWebUI 기동
EOF
else
    echo "다음 단계:"
    echo "   bash scripts/step2_1_check_docker.sh     # 설치 결과 검증"
    echo "   bash scripts/step2_2_setup_infra.sh      # 인프라 컨테이너 기동"
fi
