#!/usr/bin/env bash
# [LOCAL ONLY] 실 LLM provider (Ollama / OpenAI / Claude) 호출 필요.
# Ollama 가 떠 있고 모델 (qwen2.5:7b 등) 이 pull 돼 있어야 의미있게 실행.
# 미가동 시 자동 skip — CI 에서 도 무리 없이 SKIP 처리.
set -uo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/_lib.sh"
env_report
run_llm
print_summary
