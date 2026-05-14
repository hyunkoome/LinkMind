# OpenClaw 통합 가이드

> **요약**: OpenClaw 는 LinkMind 의 **frontend agent** (Telegram/Slack/Discord 등 채널 처리,
> agent loop 수행). LinkMind 는 **backend knowledge OS** (raw-first DB + semantic search + RAG).
> 두 시스템은 **HTTP API** 만으로 통신하며, 서로의 코드를 직접 참조하지 않는다.

---

## 1. 왜 OpenClaw 를 쓰는가

| 직접 구현 시 | OpenClaw 위임 시 |
|---|---|
| LinkMind 안에 Telegram bot, Slack bot, Discord bot, WhatsApp bot 각각 작성 | OpenClaw 가 이미 보유한 133 개 extension 활용 |
| Agent tool-use 루프 직접 구현 | OpenClaw agent core 활용 |
| Persistent memory, 채널 라우팅, 인증 토큰 관리 직접 | OpenClaw 가 이미 처리 |
| LinkMind 가 backend + frontend 두 역할 모두 짊어짐 | LinkMind 는 backend 한 가지만 잘하면 됨 |

OpenClaw 가 깨지거나 사라져도, LinkMind 는 HTTP API 만 유지하면 다른 client (Claude Desktop, Cursor, n8n, 자체 봇) 로 즉시 교체 가능. **수평 관계** 이지 종속 아님.

## 2. 통합 지점 4가지

### 2.1. 수집 (OpenClaw → LinkMind `POST /ingest`)

OpenClaw extension (예: telegram, slack, webhooks) 이 메시지/링크/파일을 받으면 LinkMind 에 POST 한다.

```bash
curl -X POST http://localhost:8000/ingest \
  -H 'Content-Type: application/json' \
  -d '{
    "source_type": "telegram",
    "raw_content": "FAST-LIO2 reflector localization 관련 새 논문: https://arxiv.org/abs/...",
    "source_id": "tg_<chat>_<message_id>",
    "source_url": "https://t.me/c/...",
    "source_metadata": {"sender": "...", "ts": "2026-05-14T10:00:00Z"},
    "analyze_now": true
  }'
```

LinkMind 는 raw-first 원칙으로 즉시 저장 + 임베딩 + 인덱싱.

### 2.2. 검색/질의응답 (OpenClaw → LinkMind `POST /search` 또는 `/ask`)

사용자가 Telegram 에서 "FAST-LIO2 자료 보여줘" 라고 하면:
1. OpenClaw 가 메시지 수신
2. OpenClaw agent 가 tool `linkmind.search` 또는 `linkmind.ask` 호출
3. LinkMind 가 RAG 결과 반환
4. OpenClaw 가 사용자에게 응답

OpenClaw tool 정의 예 (의사 코드):
```yaml
- name: linkmind_search
  description: 사용자의 개인 기술 자료에서 Semantic Search
  http:
    method: POST
    url: ${LINKMIND_API_URL}/search
    body:
      query: ${args.query}
      top_k: 8
```

### 2.3. 자체 동작 (LinkMind ↔ LinkMind, OpenClaw 무관)

Slack export 파일 import, URL 수동 ingest, 정기 batch 등 OpenClaw 없이 LinkMind 단독으로 돌릴 수 있는 작업은 모두 LinkMind 안에 둔다. 예: `python -m backend.ingest.url <url>`.

### 2.4. OpenClaw 를 AI provider 로? — **하지 않는다**

OpenClaw 는 agent (client) 이고, LinkMind 의 `LLMProvider` 추상화 (OpenAI/Claude/Ollama) 와는 레이어가 다르다. OpenClaw 를 LLMProvider 로 둘 경우 책임이 모호해지고 순환 호출 위험. AI 모델 호출은 LinkMind 가 직접 OpenAI/Claude/Ollama 로.

## 3. 설치 & 설정

### 3.1. 호스트 설치

LinkMind 의 개인 사용 시나리오 (단일 머신, OpenClaw 수정 의도 없음) 에서는 **공식 `install.sh` 가 가장 마찰이 적다** — Node.js / pnpm 등 의존성을 자동으로 bootstrap 한다.

```bash
bash scripts/install_openclaw.sh           # 기본 — curl install.sh | bash (권장)
bash scripts/install_openclaw.sh --npm     # npm/pnpm 전역 설치 (팀/CI/재현성 필요 시)
bash scripts/install_openclaw.sh --source  # external/openclaw/ 에서 dev 빌드 (OpenClaw 자체 수정)
```

세 방식 비교:

| 모드 | 동작 | 적합한 경우 |
|---|---|---|
| 기본 (install.sh) | `curl -fsSL https://openclaw.ai/install.sh \| bash`. Node 미설치 시 자동 bootstrap. | 개인 사용, set-and-forget |
| `--npm` | Node 22.16+ 또는 24+ 필요. `npm i -g openclaw@latest` 또는 `pnpm add -g openclaw@latest`. | 팀, CI, 버전 핀 필요 |
| `--source` | `cd external/openclaw && pnpm install && pnpm build`. cloned repo 사용. | OpenClaw 자체 수정/패치 |

Docker 아님 — OpenClaw 는 네이티브 Node CLI + Gateway daemon 구조.

### 3.2. Onboarding

```bash
openclaw onboard --install-daemon       # Gateway daemon 상시 기동 (launchd/systemd)
openclaw doctor                          # 환경 점검 + 정책 검사
```

`--install-daemon` 옵션이 핵심 — Gateway 가 launchd(macOS) 또는 systemd 사용자 서비스(Linux) 로 등록되어 재부팅 후에도 자동 실행됨.

### 3.3. Gateway 포트 / URL 확인

기본 포트는 18789 (README 의 quick start 예시 기준). 직접 띄울 때:

```bash
openclaw gateway --port 18789 --verbose
```

확인한 URL 을 `env/dev.env` 의 `OPENCLAW_GATEWAY_URL` 에 채우기:

```
OPENCLAW_GATEWAY_URL=http://localhost:18789
```

## 4. 코드 위치

| 위치 | 용도 |
|---|---|
| `external/openclaw/` | OpenClaw 소스 clone (gitignored, 참조용) |
| `scripts/install_openclaw.sh` | 호스트 설치 래퍼 |
| `backend/config.py` `openclaw_gateway_url` | LinkMind 가 OpenClaw 호출할 때 쓰는 URL (Phase 2 사용 예정) |
| `docs/openclaw_integration.md` | 이 문서 |

## 5. 로드맵

| Phase | 통합 작업 |
|---|---|
| Phase 1 | LinkMind HTTP API 만 명확히 노출. OpenClaw 통합 미실시. |
| Phase 2 | OpenClaw plugin/extension 작성 — `linkmind_search`, `linkmind_ingest_url` 두 가지부터. Telegram 채널에서 동작 검증. |
| Phase 3 | OpenClaw 의 webhook extension 으로 Slack 실시간 forward → LinkMind `/ingest`. |
| Phase 4 | LinkMind 에 MCP server 추가 → OpenClaw 외에 Claude Desktop / Cursor 등에서도 LinkMind 사용 가능. |

## 6. 라이센스

OpenClaw: **MIT** (https://github.com/openclaw/openclaw, Copyright 2025 Peter Steinberger)
→ 코드 참조/수정/extension 작성 자유. LinkMind 의 SaaS 화에도 호환.
