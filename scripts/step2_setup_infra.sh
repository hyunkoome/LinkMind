#!/usr/bin/env bash
# ============================================================================
# scripts/step2_setup_infra.sh
# ----------------------------------------------------------------------------
# LinkMind 인프라 컨테이너(Postgres + Qdrant + Ollama + OpenWebUI) 를 한 번에
# 띄우고 healthcheck 가 통과할 때까지 대기.
#
# 핵심 포인트:
#   - compose 파일/env-file 경로가 길어 매번 치기 번거로움 → 한 번에 처리
#   - Postgres 첫 부팅 시 backend/db/schema.sql 자동 import 가 끝나야
#     실제로 query 가능 → healthy 상태 도달까지 대기
#   - --phase2 옵션으로 TEI/MinIO 까지 띄울 수 있음
#
# 사용:
#   bash scripts/step2_setup_infra.sh                # 기본 4개 서비스
#   bash scripts/step2_setup_infra.sh --phase2       # + TEI, MinIO
#   bash scripts/step2_setup_infra.sh --recreate     # 컨테이너 강제 재생성
#
# 종료 후 검증:
#   bash scripts/step2_check_infra.sh
# ============================================================================
set -euo pipefail

# ---- 인자 파싱 -------------------------------------------------------------
PROFILE_ARGS=()
RECREATE_ARGS=()

for arg in "$@"; do
    case "$arg" in
        --phase2)   PROFILE_ARGS=(--profile phase2) ;;
        --recreate) RECREATE_ARGS=(--force-recreate) ;;
        -h|--help)
            sed -n '1,22p' "$0"
            exit 0
            ;;
        *)
            echo "❌ 알 수 없는 인자: $arg"
            exit 2
            ;;
    esac
done

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${PROJECT_ROOT}/env/dev.env"
COMPOSE_FILE="${PROJECT_ROOT}/compose/docker-compose.dev.yml"

echo "🐳 LinkMind 인프라 컨테이너 셋업 시작"
echo "    compose:  ${COMPOSE_FILE}"
echo "    env-file: ${ENV_FILE}"
if [ ${#PROFILE_ARGS[@]} -gt 0 ]; then
    echo "    profile:  phase2 (TEI + MinIO 포함)"
else
    echo "    profile:  기본 (postgres/qdrant/ollama/openwebui)"
fi
echo ""

# ---- 사전 체크 -------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
    echo "❌ docker 가 없습니다. https://docs.docker.com/engine/install/"
    exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
    echo "❌ 'docker compose' (v2 plugin) 가 없습니다. compose-plugin 설치 필요."
    exit 1
fi
if [ ! -f "$ENV_FILE" ]; then
    echo "❌ env/dev.env 가 없습니다 — cp env/dev.env.example env/dev.env 후 비밀값 채우기"
    exit 1
fi
if [ ! -f "$COMPOSE_FILE" ]; then
    echo "❌ compose 파일이 없습니다: ${COMPOSE_FILE}"
    exit 1
fi

# ---- compose up ------------------------------------------------------------
echo "🚀 docker compose up -d"
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "${PROFILE_ARGS[@]}" up -d "${RECREATE_ARGS[@]}"

# ---- healthcheck 대기 ------------------------------------------------------
# Postgres 첫 부팅 시 schema.sql 임포트가 끝나야 healthy 가 됨.
# Qdrant/Ollama 도 healthcheck 정의되어 있음. OpenWebUI 는 없어서 running 만 확인.
echo ""
echo "⏳ healthcheck 대기 중 (최대 120초)"

SERVICES_WITH_HC=(postgres qdrant ollama)
DEADLINE=$(( $(date +%s) + 120 ))
while true; do
    ALL_OK=1
    for svc in "${SERVICES_WITH_HC[@]}"; do
        CID="$(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps -q "$svc" 2>/dev/null || true)"
        if [ -z "$CID" ]; then
            ALL_OK=0; STATUS="없음"
        else
            STATUS="$(docker inspect -f '{{.State.Health.Status}}' "$CID" 2>/dev/null || echo unknown)"
            if [ "$STATUS" != "healthy" ]; then
                ALL_OK=0
            fi
        fi
        printf "  %-12s %s\n" "$svc" "$STATUS"
    done

    if [ "$ALL_OK" -eq 1 ]; then
        echo ""
        echo "✅ 모든 서비스 healthy"
        break
    fi

    if [ "$(date +%s)" -ge "$DEADLINE" ]; then
        echo ""
        echo "💥 120초 안에 healthy 도달 실패. 'docker compose logs <서비스>' 로 확인"
        exit 1
    fi
    sleep 5
    echo "  ── 재확인 ──"
done

echo ""
echo "🎉 인프라 기동 완료"
echo ""
echo "다음 단계:"
echo "  bash scripts/step2_check_infra.sh        # 연결성 + 포트 검증"
echo "  bash scripts/step3_setup_ollama.sh       # Ollama 모델 pull (qwen2.5:7b)"
echo "  python scripts/step4_init_qdrant.py      # Qdrant 컬렉션 생성"
