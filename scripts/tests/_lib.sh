#!/usr/bin/env bash
# 공통 라이브러리 — scripts/tests/each/* 와 scripts/tests/total/* 가 source 해서 사용.
#
# 직접 실행하지 마세요. each/total 안의 step 스크립트가 진입점입니다.
#
# 정책:
#   _ci_ready_  : CI (GitHub Actions, CPU-only) 에서 의미있게 도는 카테고리.
#   _local_only_: GPU / 실 LLM 호출 등 CI 가 못 흉내내는 것 — 로컬 전용.

set -uo pipefail

# 이 파일은 항상 다른 step 스크립트에서 source 됨 → BASH_SOURCE[1] 이 호출자.
_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$_LIB_DIR/../.." && pwd)"
cd "$ROOT"

PYTEST="$ROOT/.venv/bin/pytest"
[[ -x "$PYTEST" ]] || { echo "❌ .venv 없음 — bash scripts/step1_install_base_env.sh 먼저"; exit 1; }

# ── 색 출력 ───────────────────────────────────────────────────
if [[ -t 1 ]]; then
    C_R=$'\e[31m'; C_G=$'\e[32m'; C_Y=$'\e[33m'; C_B=$'\e[34m'
    C_DIM=$'\e[2m'; C_BOLD=$'\e[1m'; C_0=$'\e[0m'
else
    C_R=; C_G=; C_Y=; C_B=; C_DIM=; C_BOLD=; C_0=
fi

# ── 환경 점검 ────────────────────────────────────────────────

API_BASE="${LINKMIND_API_BASE:-http://localhost:8000}"
OLLAMA_BASE="${OLLAMA_BASE_URL_LOCAL:-http://localhost:11434}"

check_backend() {
    curl -s --max-time 2 "$API_BASE/health" > /dev/null 2>&1
}
check_ollama() {
    curl -s --max-time 2 "$OLLAMA_BASE/api/tags" > /dev/null 2>&1
}
check_gpu() {
    command -v nvidia-smi > /dev/null 2>&1 && nvidia-smi -L > /dev/null 2>&1
}
check_embedding_deps() {
    "$ROOT/.venv/bin/python" - <<'PY' 2>/dev/null
try:
    import sentence_transformers, torch  # noqa: F401
except Exception:
    raise SystemExit(1)
PY
}

env_report() {
    echo "${C_B}── 환경 점검 ──${C_0}"
    if check_backend; then
        echo "  ${C_G}✓${C_0} backend ($API_BASE)"
    else
        echo "  ${C_Y}-${C_0} backend ($API_BASE)   ${C_DIM}— integration 자동 skip${C_0}"
    fi
    if check_ollama; then
        echo "  ${C_G}✓${C_0} ollama  ($OLLAMA_BASE)"
    else
        echo "  ${C_Y}-${C_0} ollama  ($OLLAMA_BASE)   ${C_DIM}— llm 자동 skip${C_0}"
    fi
    if check_gpu; then
        echo "  ${C_G}✓${C_0} GPU     ($(nvidia-smi -L | head -1 | sed 's/UUID:.*//'))"
    else
        echo "  ${C_Y}-${C_0} GPU                              ${C_DIM}— gpu 카테고리 skip${C_0}"
    fi
    if check_embedding_deps; then
        echo "  ${C_G}✓${C_0} sentence-transformers + torch"
    else
        echo "  ${C_Y}-${C_0} sentence-transformers/torch    ${C_DIM}— embedding/gpu 카테고리 skip${C_0}"
    fi
    echo
}

# ── 카테고리 헤더 + 결과 기록 ────────────────────────────────

# 외부에서 결과를 모을 수 있도록 — 한 step 스크립트가 단독 실행할 땐 자체 print.
declare -ga TEST_RESULTS=()

_record() {
    # name | status (PASS/FAIL/SKIP/EMPTY) | duration | note
    TEST_RESULTS+=("$1|$2|$3|${4:-}")
}

# 사용:
#   run_category <name> <label> <pytest args...>
run_category() {
    local name="$1"; shift
    local label="$1"; shift
    echo
    echo "${C_B}▶ ${C_BOLD}${name}${C_0}  ${C_DIM}(${label})${C_0}"
    local t0; t0=$(date +%s)
    if "$PYTEST" "$@" --tb=short; then
        local dt=$(( $(date +%s) - t0 ))
        echo "  ${C_G}✅ ${name} pass (${dt}s)${C_0}"
        _record "$name" "PASS" "${dt}s"
    else
        local rc=$?
        local dt=$(( $(date +%s) - t0 ))
        if [[ $rc -eq 5 ]]; then
            echo "  ${C_Y}- ${name} skipped (marker 매칭 0)${C_0}"
            _record "$name" "EMPTY" "${dt}s" "no tests"
        else
            echo "  ${C_R}❌ ${name} FAIL (rc=$rc, ${dt}s)${C_0}"
            _record "$name" "FAIL" "${dt}s" "exit $rc"
        fi
    fi
}

print_summary() {
    [[ ${#TEST_RESULTS[@]} -eq 0 ]] && return 0
    echo
    echo "${C_B}═══════ 종합 ═══════${C_0}"
    printf "  %-14s  %-5s  %-6s  %s\n" "Category" "Status" "Time" "Note"
    printf "  %-14s  %-5s  %-6s  %s\n" "--------" "------" "----" "----"
    local has_fail=0
    for line in "${TEST_RESULTS[@]}"; do
        IFS='|' read -r name status dt note <<< "$line"
        local color=""
        case "$status" in
            PASS)  color="$C_G" ;;
            FAIL)  color="$C_R"; has_fail=1 ;;
            SKIP)  color="$C_Y" ;;
            EMPTY) color="$C_DIM" ;;
        esac
        printf "  %-14s  ${color}%-5s${C_0}  %-6s  %s\n" "$name" "$status" "$dt" "$note"
    done
    echo
    if [[ $has_fail -eq 1 ]]; then
        echo "${C_R}일부 FAIL — 로그 확인.${C_0}"
        return 1
    fi
    echo "${C_G}모든 실행된 카테고리 pass.${C_0}"
    return 0
}

# ── 카테고리 실행 함수 (각 step 스크립트가 호출) ───────────

run_cpu() {
    # 가장 좁은 default — marker 없는 pure unit/mock 만.
    run_category "cpu" "CPU + mock, ≈4s" \
        -m "not gpu and not embedding and not integration and not llm" \
        tests/ -v
}

run_embedding() {
    if ! check_embedding_deps; then
        echo "${C_Y}⚠️  sentence-transformers/torch 미설치 — skip${C_0}"
        _record "embedding" "SKIP" "0s" "deps 없음"
        return
    fi
    run_category "embedding" "MiniLM CPU smoke, ≈15s" \
        -m embedding tests/ -v
}

run_integration() {
    if ! check_backend; then
        echo "${C_Y}⚠️  backend 미가동 (${API_BASE}) — skip${C_0}"
        echo "    먼저: ${C_DIM}bash scripts/step5_run_dev.sh --backend-only${C_0}"
        _record "integration" "SKIP" "0s" "backend 미가동"
        return
    fi
    run_category "integration" "backend e2e" \
        -m integration tests/integration/ -v
}

run_llm() {
    if ! check_ollama; then
        echo "${C_Y}⚠️  Ollama 미가동 (${OLLAMA_BASE}) — skip${C_0}"
        _record "llm" "SKIP" "0s" "ollama 미가동"
        return
    fi
    run_category "llm" "실 LLM 호출" -m llm tests/ -v
}

run_gpu() {
    if ! check_gpu; then
        echo "${C_Y}⚠️  GPU 없음 — skip (CI 환경에서는 정상)${C_0}"
        _record "gpu" "SKIP" "0s" "GPU 없음"
        return
    fi
    if ! check_embedding_deps; then
        echo "${C_Y}⚠️  sentence-transformers/torch 미설치 — skip${C_0}"
        _record "gpu" "SKIP" "0s" "deps 없음"
        return
    fi
    run_category "gpu" "CUDA + MiniLM" -m gpu tests/ -v
}
