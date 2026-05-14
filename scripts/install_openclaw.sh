#!/usr/bin/env bash
# ============================================================================
# OpenClaw 호스트 설치 래퍼.
# ----------------------------------------------------------------------------
# LinkMind 는 OpenClaw 에 직접 의존하지 않지만, OpenClaw 를 frontend agent 로
# 사용하는 시나리오(Telegram/Slack/Discord/WhatsApp 채널 입력 처리)에서는
# 호스트에 설치되어 있어야 한다.
#
# 설치 방식 (LinkMind 의 개인 사용 시나리오에서 기본은 install.sh):
#
#   bash scripts/install_openclaw.sh            # 기본 — 공식 install.sh 사용 (zero-friction)
#   bash scripts/install_openclaw.sh --npm      # npm/pnpm 전역 설치 (팀/CI/재현성)
#   bash scripts/install_openclaw.sh --source   # external/openclaw/ 에서 dev 빌드 (OpenClaw 자체 수정)
#
# 참고:
#   - 공식 install.sh : https://openclaw.ai/install.sh
#   - GitHub repo     : https://github.com/openclaw/openclaw (MIT)
#   - cloned 소스     : external/openclaw/ (gitignored, 참조용)
# ============================================================================
set -euo pipefail

MODE="install_sh"
for arg in "$@"; do
    case "$arg" in
        --npm)    MODE="npm" ;;
        --source) MODE="source" ;;
        -h|--help)
            sed -n '1,25p' "$0"
            exit 0
            ;;
        *)
            echo "❌ 알 수 없는 인자: $arg"
            sed -n '1,25p' "$0"
            exit 2
            ;;
    esac
done

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SOURCE_DIR="${PROJECT_ROOT}/external/openclaw"

# ----------------------------------------------------------------------------
# 이미 설치돼 있으면 skip (source 모드 제외)
# ----------------------------------------------------------------------------
if [ "${MODE}" != "source" ] && command -v openclaw >/dev/null 2>&1; then
    echo "✅ openclaw 이미 설치됨: $(openclaw --version 2>/dev/null || echo unknown)"
    echo "   업데이트:"
    echo "     • install.sh 방식 → 이 스크립트 다시 실행 (또는 'openclaw self update')"
    echo "     • npm 방식        → 'npm i -g openclaw@latest' (또는 'pnpm add -g openclaw@latest')"
    exit 0
fi

# ============================================================================
# 모드 1: 기본 — 공식 install.sh (LinkMind 권장)
# ============================================================================
if [ "${MODE}" = "install_sh" ]; then
    echo "📦 OpenClaw 공식 install.sh 실행"
    echo "    - Node.js / pnpm 등 필요한 의존성 자동 bootstrap"
    echo "    - 출처: https://openclaw.ai/install.sh"
    echo ""
    curl -fsSL https://openclaw.ai/install.sh | bash

    echo ""
    echo "✅ 설치 완료"
    echo ""
    echo "다음 단계:"
    echo "  1) openclaw onboard --install-daemon"
    echo "       └─ Gateway daemon(launchd/systemd 사용자 서비스)으로 상시 기동"
    echo "  2) openclaw doctor"
    echo "       └─ 환경 점검 + DM 정책 안전성 검사"
    echo "  3) Gateway 포트 확인 후 env/dev.env 의 OPENCLAW_GATEWAY_URL 업데이트"
    echo "       └─ 기본 포트 예시: --port 18789"
    echo ""
    echo "📖 자세한 통합 가이드: docs/openclaw_integration.md"
    exit 0
fi

# ============================================================================
# 모드 2: --npm — npm/pnpm 전역 설치 (재현성/팀 환경)
# ============================================================================
if [ "${MODE}" = "npm" ]; then
    # Node 버전 검사 (22.16+ 또는 24+ 권장)
    need_node() {
        command -v node >/dev/null 2>&1 || return 1
        local v major minor
        v="$(node -v | sed 's/^v//')"
        major="${v%%.*}"
        minor="$(echo "$v" | cut -d. -f2)"
        [ "$major" -ge 24 ] && return 0
        [ "$major" -eq 22 ] && [ "$minor" -ge 16 ] && return 0
        return 1
    }

    if ! need_node; then
        echo "⚠️  Node.js 22.16+ 또는 24+ 가 필요합니다. (현재: $(node -v 2>/dev/null || echo 'not installed'))"
        echo ""
        echo "    설치 방법 (택1):"
        echo "      • nvm:  curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash"
        echo "              source ~/.bashrc && nvm install 24 && nvm use 24"
        echo "      • apt:  curl -fsSL https://deb.nodesource.com/setup_24.x | sudo -E bash -"
        echo "              sudo apt-get install -y nodejs"
        echo ""
        echo "    또는 그냥 기본 모드 (install.sh) 를 쓰면 Node 도 자동 설치됩니다:"
        echo "      bash scripts/install_openclaw.sh"
        exit 1
    fi

    echo "✅ Node.js OK: $(node -v)"
    if command -v pnpm >/dev/null 2>&1; then
        echo "✅ pnpm OK: $(pnpm -v)"
        echo ""
        echo "📦 pnpm 으로 OpenClaw 전역 설치"
        pnpm add -g openclaw@latest
    elif command -v npm >/dev/null 2>&1; then
        echo "✅ npm OK: $(npm -v)  (pnpm 권장: 'npm i -g pnpm')"
        echo ""
        echo "📦 npm 으로 OpenClaw 전역 설치"
        npm install -g openclaw@latest
    else
        echo "❌ npm/pnpm 둘 다 없음. Node 설치가 깨졌을 수 있음."
        exit 1
    fi

    echo ""
    echo "✅ 설치 완료: $(openclaw --version 2>/dev/null || echo unknown)"
    echo ""
    echo "다음 단계: openclaw onboard --install-daemon  →  openclaw doctor"
    echo "📖 docs/openclaw_integration.md"
    exit 0
fi

# ============================================================================
# 모드 3: --source — cloned external/openclaw 에서 dev 빌드 (OpenClaw 수정용)
# ============================================================================
if [ "${MODE}" = "source" ]; then
    if [ ! -d "${SOURCE_DIR}" ]; then
        echo "❌ ${SOURCE_DIR} 가 없습니다. 먼저 clone:"
        echo "   git clone https://github.com/openclaw/openclaw.git external/openclaw"
        exit 1
    fi

    if ! command -v pnpm >/dev/null 2>&1; then
        echo "⚠️  OpenClaw 저장소는 pnpm workspace 입니다. pnpm 설치 필요:"
        echo "    npm i -g pnpm"
        exit 1
    fi

    echo "🔧 cloned source 에서 OpenClaw 빌드 (개발자 모드)"
    cd "${SOURCE_DIR}"
    pnpm install
    pnpm build || true

    echo ""
    echo "✅ 빌드 완료. 실행은 다음 중 하나:"
    echo "    cd ${SOURCE_DIR} && pnpm openclaw onboard --install-daemon"
    echo "    또는 글로벌 심볼릭링크: pnpm link --global  (그 후 'openclaw' 명령 사용)"
    exit 0
fi
