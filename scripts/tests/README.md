# LinkMind 테스트 스크립트

`scripts/tests/` 의 폴더는 **어느 환경에서 도는지** 한 눈에 보이도록 분리:

| 디렉토리 | 대상 환경 | 비고 |
|---------|----------|------|
| `ci/`   | GitHub Actions + 로컬 | CPU + mock 기반. CI 가 매 push 마다 실행. |
| `local/`| 로컬 전용             | GPU / 실 LLM 호출 필요. CI 에선 환경 미충족 → 자동 skip. |
| `total/`| —                    | 위의 스크립트들을 묶어 한 번에 실행 + 종합 요약. |

## 카테고리 (step 순서 = 가벼움 → 무거움)

| Step | 카테고리 | 환경 | 위치 | 시간 |
|------|---------|------|------|------|
| 1 | `cpu`         | CI ready  | `ci/step1_cpu.sh`           | ≈4s |
| 2 | `embedding`   | CI ready  | `ci/step2_embedding.sh`     | ≈15s (첫 회 모델 다운로드 ~80MB) |
| 3 | `integration` | CI ready  | `ci/step3_integration.sh`   | ≈1s (backend 미가동 시 SKIP) |
| 4 | `llm`         | LOCAL only| `local/step4_llm.sh`        | 분 단위 (실 LLM 호출, Ollama 필요) |
| 5 | `gpu`         | LOCAL only| `local/step5_gpu.sh`        | ≈10s (CUDA 필요) |

## 사용법

```bash
# 한 카테고리만
bash scripts/tests/ci/step1_cpu.sh
bash scripts/tests/ci/step2_embedding.sh
bash scripts/tests/ci/step3_integration.sh
bash scripts/tests/local/step4_llm.sh
bash scripts/tests/local/step5_gpu.sh

# 로컬에서 5 카테고리 모두 (환경 미충족이면 SKIP)
bash scripts/tests/total/run_all_local.sh

# CI 가 도는 것만 시뮬레이션 (push 전 점검)
bash scripts/tests/total/run_ci_simulation.sh
```

## 동작 정책

- 각 스크립트는 자체적으로 환경 점검 (backend / Ollama / GPU / 의존성) 후 의미있게
  실행할 수 없는 카테고리는 **SKIP** 으로 표시 — FAIL 이 아님.
- 종료 코드: 모든 실행된 카테고리가 PASS 면 0, FAIL 이 있으면 1.
- `pytest.ini` 의 marker 정책:
  - `gpu` — CUDA 강제, CI 에선 deselect.
  - `embedding` — CPU 가능한 sentence-transformers smoke.
  - `integration` — backend 가 떠 있을 때만 (fixture 가 skip).
  - `llm` — 실 LLM provider 호출 (fixture 가 skip).
  - 마커 없는 default = `cpu` 카테고리.

## CI 와의 매핑

GitHub Actions (`.github/workflows/ci.yml`) 는 `pytest -m "not gpu"` 로 한 번에
실행 — `total/run_ci_simulation.sh` 와 결과가 동등 (각 카테고리가 환경 미충족 시
fixture 가 skip 처리).
