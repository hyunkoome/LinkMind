#!/usr/bin/env bash
# ============================================================================
# scripts/slack_export.sh
# ----------------------------------------------------------------------------
# slackdump 로 Slack workspace 전체 export → archive/slack_export/full_<ts>/
# 폴더명에 HH-MM-SS 포함하여 같은 날 여러 번 export 해도 충돌 없음.
# `archive/slack_export/latest` symlink 가 항상 최신 export 를 가리킴.
#
# 사용:
#   bash scripts/slack_export.sh                       # workspace=hkkim, type=standard, files=true
#   bash scripts/slack_export.sh --workspace=other
#   bash scripts/slack_export.sh --public              # 공개 채널만 (-type public)
#   bash scripts/slack_export.sh --no-files            # 첨부 제외
#   bash scripts/slack_export.sh --out=archive/slack_export/manual_path
#   bash scripts/slack_export.sh --test=C06QLDC2G72    # 한 채널만 (디버깅용)
#
# 사전 조건:
#   - slackdump 설치됨 (apt: 'sudo apt install slackdump' 또는
#     go install github.com/rusq/slackdump/v4@latest)
#   - workspace alias 가 등록되어 있어야 함 (slackdump workspace list)
#     없으면 안내 메시지 후 종료. 등록은 docs/slack_setup.md 참고.
# ============================================================================
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${PROJECT_ROOT}/env/dev.env"
ARCHIVE_DIR="${PROJECT_ROOT}/archive/slack_export"

# env 로드 (SLACKDUMP_WORKSPACE 등 오버라이드 가능)
if [ -f "$ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    . "$ENV_FILE"
    set +a
fi

WORKSPACE="${SLACKDUMP_WORKSPACE:-hkkim}"
TYPE="standard"
FILES="true"
OUT_DIR=""
TEST_CHANNEL=""

show_help() { sed -n '2,22p' "$0"; }

for arg in "$@"; do
    case "$arg" in
        --workspace=*) WORKSPACE="${arg#*=}" ;;
        --public)      TYPE="public" ;;
        --no-files)    FILES="false" ;;
        --out=*)       OUT_DIR="${arg#*=}" ;;
        --test=*)      TEST_CHANNEL="${arg#*=}" ;;
        -h|--help)     show_help; exit 0 ;;
        *)
            echo "❌ 알 수 없는 인자: $arg"
            show_help
            exit 2
            ;;
    esac
done

# ---- slackdump 설치 확인 ---------------------------------------------------
if ! command -v slackdump >/dev/null 2>&1; then
    cat <<'EOF'
❌ slackdump 가 설치되어 있지 않습니다.
   설치 (택1):
     sudo apt install slackdump
     # 또는 Go 설치 후
     go install github.com/rusq/slackdump/v4@latest
     # 또는 GitHub release 바이너리 다운로드
     # https://github.com/rusq/slackdump/releases
EOF
    exit 1
fi

# ---- workspace 등록 확인 ---------------------------------------------------
# 'slackdump workspace list' 출력: "=> hkkim (file: hkkim.bin, last modified: ...)"
# 단어 경계 + "(file:" 가 따라오는 패턴으로 워크스페이스명 매칭.
if ! slackdump workspace list 2>/dev/null \
        | grep -qE "(^|[[:space:]])${WORKSPACE}[[:space:]]+\(file:"; then
    cat <<EOF
❌ workspace alias '${WORKSPACE}' 가 slackdump 에 등록되어 있지 않습니다.

   env/dev.env 의 SLACK_USER_TOKEN(xoxc-...) 과 SLACK_D_COOKIE 로 등록:
     source env/dev.env
     slackdump workspace new -token "\$SLACK_USER_TOKEN" -cookie "\$SLACK_D_COOKIE" ${WORKSPACE}

   토큰 추출 가이드: docs/slack_setup.md
EOF
    exit 1
fi

# ---- 출력 경로 (timestamp 까지) --------------------------------------------
TIMESTAMP="$(date +%Y-%m-%d_%H-%M-%S)"

if [ -z "$OUT_DIR" ]; then
    if [ -n "$TEST_CHANNEL" ]; then
        OUT_DIR="${ARCHIVE_DIR}/test_${TIMESTAMP}"
    else
        OUT_DIR="${ARCHIVE_DIR}/full_${TIMESTAMP}"
    fi
fi
LOG_FILE="${OUT_DIR}.log"

mkdir -p "$ARCHIVE_DIR"

echo "📦 Slack Export 시작"
echo "    workspace: ${WORKSPACE}"
echo "    type:      ${TYPE}"
echo "    files:     ${FILES}"
echo "    out:       ${OUT_DIR}"
echo "    log:       ${LOG_FILE}"
if [ -n "$TEST_CHANNEL" ]; then
    echo "    채널 한정: ${TEST_CHANNEL} (테스트 모드)"
fi
echo ""

# ---- 실행 ------------------------------------------------------------------
CMD=(slackdump export
    -workspace "$WORKSPACE"
    -type "$TYPE"
    "-files=${FILES}"
    -o "$OUT_DIR"
    -v
)
if [ -n "$TEST_CHANNEL" ]; then
    CMD+=("$TEST_CHANNEL")
fi

# pipefail 덕에 slackdump 실패 시 종료. tee 로 로그 동시 저장.
"${CMD[@]}" 2>&1 | tee "$LOG_FILE"

# ---- latest symlink --------------------------------------------------------
ln -sfn "$(basename "$OUT_DIR")" "${ARCHIVE_DIR}/latest"

echo ""
echo "✅ Export 완료: ${OUT_DIR}"
echo "🔗 ${ARCHIVE_DIR}/latest → $(basename "$OUT_DIR")"

# ---- 간단 통계 -------------------------------------------------------------
if [ -d "$OUT_DIR" ]; then
    MSGS=$(find "$OUT_DIR" -name '*.json' -not -path '*/attachments/*' 2>/dev/null | wc -l)
    ATTS=$(find "$OUT_DIR/attachments" -type f 2>/dev/null | wc -l)
    SIZE=$(du -sh "$OUT_DIR" 2>/dev/null | awk '{print $1}')
    echo "📊 ${SIZE}, message files=${MSGS}, attachments=${ATTS}"
fi
