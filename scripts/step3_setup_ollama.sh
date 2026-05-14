#!/usr/bin/env bash
# ============================================================================
# scripts/step3_setup_ollama.sh
# ----------------------------------------------------------------------------
# LinkMind 의 Ollama 서비스를 처음 띄울 때 사용하는 셋업 스크립트.
#
# Ollama 는 LinkMind 의 docker-compose.dev.yml 에 이미 정의되어 있고
# RTX 4090 GPU passthrough 도 설정되어 있다. 이 스크립트는:
#   1) docker / docker compose 가 동작하는지 확인
#   2) ollama 컨테이너가 떠 있지 않으면 띄움
#   3) API 가 응답할 때까지 대기 (헬스체크)
#   4) env/dev.env 의 OLLAMA_MODEL 을 컨테이너 안으로 pull
#   5) 간단한 프롬프트로 동작 검증
#
# 사용:
#   bash scripts/step3_setup_ollama.sh                  # OLLAMA_MODEL 기본값 사용
#   bash scripts/step3_setup_ollama.sh qwen2.5:14b      # 모델 직접 지정
#   bash scripts/step3_setup_ollama.sh --no-pull        # 컨테이너만 띄우고 모델 pull 안 함
#
# 이미 떠 있는 컨테이너에 추가 모델만 받고 싶으면:
#   bash scripts/ollama_pull.sh qwen2.5:14b
# ============================================================================
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE_FILE="${PROJECT_ROOT}/compose/docker-compose.dev.yml"
ENV_FILE="${PROJECT_ROOT}/env/dev.env"
CONTAINER_NAME="linkmind-ollama"

# ----------------------------------------------------------------------------
# 인자 파싱
# ----------------------------------------------------------------------------
MODEL=""
DO_PULL=1
for arg in "$@"; do
    case "$arg" in
        --no-pull) DO_PULL=0 ;;
        -h|--help)
            sed -n '1,22p' "$0"
            exit 0
            ;;
        *)
            if [ -z "$MODEL" ]; then
                MODEL="$arg"
            else
                echo "❌ 알 수 없는 인자: $arg"
                exit 2
            fi
            ;;
    esac
done

# 기본 모델: env/dev.env 의 OLLAMA_MODEL → 그것도 없으면 qwen2.5:7b
if [ -z "$MODEL" ]; then
    if [ -f "$ENV_FILE" ]; then
        MODEL=$(grep -E '^OLLAMA_MODEL=' "$ENV_FILE" | head -1 | cut -d= -f2- || true)
    fi
    [ -z "$MODEL" ] && MODEL="qwen2.5:7b"
fi

echo "🦙 Ollama 셋업 시작"
echo "    프로젝트 루트:  ${PROJECT_ROOT}"
echo "    compose 파일:   ${COMPOSE_FILE}"
echo "    env 파일:       ${ENV_FILE}"
echo "    모델:           ${MODEL}"
echo ""

# ----------------------------------------------------------------------------
# 1. Docker / Docker Compose 가용성 체크
# ----------------------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
    echo "❌ docker 명령이 없습니다. https://docs.docker.com/engine/install/ubuntu/ 참고."
    exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
    echo "❌ docker compose v2 가 필요합니다. 'docker compose version' 실패."
    exit 1
fi
if ! docker info >/dev/null 2>&1; then
    echo "❌ Docker daemon 이 실행 중이 아닙니다. 'sudo systemctl start docker' 또는 Docker Desktop 시작."
    exit 1
fi
echo "✅ Docker 확인: $(docker --version | cut -d, -f1)"

# GPU 사용 가능 여부 (경고만 — CPU 로도 작동은 함)
if command -v nvidia-smi >/dev/null 2>&1; then
    echo "✅ NVIDIA GPU 감지: $(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)"
else
    echo "⚠️  nvidia-smi 없음 — GPU 가속 없이 CPU 로만 동작 (느림)"
fi

# ----------------------------------------------------------------------------
# 2. ollama 컨테이너 띄우기 (이미 떠있으면 skip)
# ----------------------------------------------------------------------------
echo ""
echo "🚀 ollama 컨테이너 상태 확인"
if docker ps --format '{{.Names}}' | grep -qx "${CONTAINER_NAME}"; then
    echo "✅ ${CONTAINER_NAME} 이미 실행 중"
else
    if [ ! -f "$ENV_FILE" ]; then
        echo "❌ ${ENV_FILE} 가 없습니다. env/dev.env.example 을 복사해서 채우세요."
        exit 1
    fi
    echo "🔄 ollama (+ openwebui) 시작"
    docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d ollama openwebui
fi

# ----------------------------------------------------------------------------
# 3. API 가 응답할 때까지 대기 (최대 120초)
# ----------------------------------------------------------------------------
echo ""
echo "⏳ Ollama API 응답 대기 (http://localhost:11434)"
for i in $(seq 1 60); do
    if curl -fsS http://localhost:11434/api/tags >/dev/null 2>&1; then
        echo "✅ Ollama API 응답 OK"
        break
    fi
    if [ "$i" -eq 60 ]; then
        echo "❌ 120초 대기 후에도 응답 없음. 로그 확인: docker logs ${CONTAINER_NAME}"
        exit 1
    fi
    printf "."
    sleep 2
done

# ----------------------------------------------------------------------------
# 4. 모델 pull
# ----------------------------------------------------------------------------
if [ "$DO_PULL" -eq 1 ]; then
    echo ""
    echo "📥 모델 pull: ${MODEL} (이미 받아져 있으면 빠르게 끝남)"
    if docker exec "${CONTAINER_NAME}" ollama list 2>/dev/null | awk 'NR>1 {print $1}' | grep -qx "${MODEL}"; then
        echo "✅ ${MODEL} 이미 컨테이너에 존재 — pull 생략"
    else
        docker exec -it "${CONTAINER_NAME}" ollama pull "${MODEL}"
    fi
fi

# ----------------------------------------------------------------------------
# 5. 간단한 검증 — 핑 프롬프트
# ----------------------------------------------------------------------------
echo ""
echo "🧪 동작 검증: 짧은 프롬프트 호출"
RESPONSE=$(curl -fsS -X POST http://localhost:11434/api/chat \
    -H 'Content-Type: application/json' \
    -d "{\"model\":\"${MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"한 단어로 인사해주세요\"}],\"stream\":false}" \
    | python3 -c 'import sys,json; print(json.load(sys.stdin).get("message",{}).get("content","(no response)"))' 2>/dev/null) || RESPONSE="(검증 호출 실패)"

echo "    모델 응답: ${RESPONSE}"

# ----------------------------------------------------------------------------
# 요약
# ----------------------------------------------------------------------------
echo ""
echo "🎉 셋업 완료"
echo ""
echo "현재 컨테이너에 받아져 있는 모델:"
docker exec "${CONTAINER_NAME}" ollama list 2>/dev/null | sed 's/^/    /'
echo ""
echo "다음 단계:"
echo "  • Ollama 검증:              bash scripts/step3_check_ollama.sh"
echo "  • Qdrant 컬렉션 생성:       python scripts/step4_init_qdrant.py"
echo "  • LinkMind 백엔드 띄우기:  uvicorn backend.main:app --reload"
echo "  • 다른 모델 추가 받기:     bash scripts/ollama_pull.sh <model>"
echo "  • Ollama 채팅 UI:           http://localhost:3000  (OpenWebUI)"
echo "  • Ollama API 직접 호출:    http://localhost:11434/api/chat"
