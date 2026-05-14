#!/usr/bin/env bash
# ============================================================================
# OpenClaw 호스트 설치 래퍼.
# ----------------------------------------------------------------------------
# LinkMind는 OpenClaw에 직접 의존하지 않지만, OpenClaw를 frontend agent로
# 사용하는 시나리오(Telegram/Slack/Discord/WhatsApp 채널 입력 처리)에서는
# 호스트에 설치되어 있어야 한다.
#
# 사용:
#   bash scripts/install_openclaw.sh
#   bash scripts/install_openclaw.sh --source    # cloned external/openclaw에서 dev 빌드 사용
#
# 참고:
#   - external/openclaw/README.md  (공식 install 권고: npm/pnpm 전역 설치)
#   - external/openclaw/CLAUDE.md  (개발자 가이드)
# ============================================================================
set -euo pipefail

USE_SOURCE=0
for arg in "$@"; do
    case "$arg" in
        --source) USE_SOURCE=1 ;;
        -h|--help)
            sed -n '1,30p' "$0"
            exit 0
            ;;
    esac
done

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SOURCE_DIR="${PROJECT_ROOT}/external/openclaw"

# ----------------------------------------------------------------------------
# 0. 이미 설치돼 있으면 skip
# ----------------------------------------------------------------------------
if [ "${USE_SOURCE}" -eq 0 ] && command -v openclaw >/dev/null 2>&1; then
    echo "✅ openclaw 이미 설치됨: $(openclaw --version 2>/dev/null || echo unknown)"
    echo "   업데이트: openclaw doctor 후 'pnpm add -g openclaw@latest' 또는 'npm i -g openclaw@latest'"
    exit 0
fi

# ----------------------------------------------------------------------------
# 1. Node.js 버전 체크 (22.16+ 또는 24+ 권장)
# ----------------------------------------------------------------------------
need_node() {
    if ! command -v node >/dev/null 2>&1; then
        return 1
    fi
    local v
    v="$(node -v | sed 's/^v//')"
    local major minor
    major="${v%%.*}"
    minor="$(echo "$v" | cut -d. -f2)"
    # Node 24+ OK
    if [ "$major" -ge 24 ]; then return 0; fi
    # Node 22.16+ OK
    if [ "$major" -eq 22 ] && [ "$minor" -ge 16 ]; then return 0; fi
    return 1
}

if ! need_node; then
    echo "⚠️  Node.js 22.16+ 또는 24+ 가 필요합니다. (현재: $(node -v 2>/dev/null || echo 'not installed'))"
    echo ""
    echo "    설치 방법 (택1):"
    echo "      • nvm:    curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash"
    echo "                source ~/.bashrc && nvm install 24 && nvm use 24"
    echo "      • apt:    curl -fsSL https://deb.nodesource.com/setup_24.x | sudo -E bash -"
    echo "                sudo apt-get install -y nodejs"
    echo ""
    exit 1
fi

echo "✅ Node.js OK: $(node -v)"

# ----------------------------------------------------------------------------
# 2. pnpm 우선, 없으면 npm 사용
# ----------------------------------------------------------------------------
PKG_CMD=""
if command -v pnpm >/dev/null 2>&1; then
    PKG_CMD="pnpm"
    echo "✅ pnpm OK: $(pnpm -v)"
elif command -v npm >/dev/null 2>&1; then
    PKG_CMD="npm"
    echo "✅ npm OK: $(npm -v)  (pnpm 권장: 'npm i -g pnpm')"
else
    echo "❌ npm/pnpm 둘 다 없음. Node 설치가 깨졌을 수 있음."
    exit 1
fi

# ----------------------------------------------------------------------------
# 3-A. --source : external/openclaw/ 에서 직접 빌드 (개발자/패치용)
# ----------------------------------------------------------------------------
if [ "${USE_SOURCE}" -eq 1 ]; then
    if [ ! -d "${SOURCE_DIR}" ]; then
        echo "❌ ${SOURCE_DIR} 가 없습니다. 먼저 clone:"
        echo "   git clone https://github.com/openclaw/openclaw.git external/openclaw"
        exit 1
    fi
    echo ""
    echo "🔧 cloned source에서 OpenClaw 빌드 (개발자 모드)"
    cd "${SOURCE_DIR}"

    if [ "${PKG_CMD}" != "pnpm" ]; then
        echo "⚠️  openclaw 저장소는 pnpm workspace 입니다. pnpm 설치:"
        echo "    npm i -g pnpm"
        exit 1
    fi

    pnpm install
    # 빌드는 명시 — 처음 실행 시 lazy compile 시 시간 오래 걸림.
    pnpm build || true

    echo ""
    echo "✅ 빌드 완료. 실행은 다음 중 하나:"
    echo "    cd ${SOURCE_DIR} && pnpm openclaw onboard --install-daemon"
    echo "    또는 글로벌 심볼릭링크: pnpm link --global  (그 후 'openclaw' 명령 사용)"
    echo ""
    exit 0
fi

# ----------------------------------------------------------------------------
# 3-B. 기본 : openclaw 전역 설치
#   README.md 의 권고 방법:
#     npm install -g openclaw@latest
#     # or: pnpm add -g openclaw@latest
# ----------------------------------------------------------------------------
echo ""
echo "📦 OpenClaw 전역 설치 (${PKG_CMD})"

if [ "${PKG_CMD}" = "pnpm" ]; then
    pnpm add -g openclaw@latest
else
    npm install -g openclaw@latest
fi

echo ""
echo "✅ 설치 완료: $(openclaw --version 2>/dev/null || echo unknown)"
echo ""
echo "다음 단계:"
echo "  1) openclaw onboard --install-daemon"
echo "       └─ Gateway daemon(launchd/systemd 사용자 서비스)으로 상시 기동"
echo "  2) openclaw doctor"
echo "       └─ 환경 점검 + 정책 안전성 검사"
echo "  3) Gateway 포트 확인 후 env/dev.env 의 OPENCLAW_GATEWAY_URL 업데이트"
echo "       └─ 기본 포트 예시: --port 18789"
echo ""
echo "📖 자세한 통합 가이드: docs/openclaw_integration.md"
echo "📖 공식 채널/플러그인 문서: external/openclaw/docs/"
