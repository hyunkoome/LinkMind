# Agent 아키텍처 가이드

> **요약**: LinkMind 는 **self-contained personal AI engine** 이다 — backend + agent + UI 를
> 같은 저장소에서 같이 유지한다. 외부 client agent (openclaw / hermes-agent 등) 에
> 의존하지 않는다. self-host 한 방으로 다 따라온다. 외부 프로젝트는 **벤치마킹 참조**
> 용도 (`external/`, gitignored). 이전 `openclaw_integration.md` 의 "OpenClaw 가 frontend
> agent" 가정은 폐기됐다.

---

## 1. 왜 단일 self-contained 시스템인가

| 분리형 (이전) | 단일 self-contained (현재) |
|---|---|
| LinkMind + OpenClaw 두 시스템 별도 설치 | `docker compose up` 한 번으로 끝 |
| OpenClaw 의존성 (Node 22.16+, daemon 등록) | Python venv + Docker 만 |
| 사용자가 두 시스템 운영 부담 | LinkMind 한 군데만 운영 |
| OpenClaw 가 깨지면 채널 입력 끊김 | LinkMind 가 자체 channel agent 보유 |
| SaaS 화 시 OpenClaw 관계 모호 | LinkMind 가 단일 배포 단위 |

→ 사용자가 personal AI engine 한 개를 self-host 한다는 비전 (CLAUDE.md §1) 에 일치.

다만 **모듈 경계는 명확히 분리** — `backend/` ↔ `ai_agents/` ↔ `frontend/` ↔ `frontend_v2/`
가 같은 venv 안에 있되 독립 진입점.

---

## 2. 모듈 구조

```
LinkMind/
├─ backend/                  # HTTP API + DB + LLM + ingest
│  ├─ api/                  # /ingest /search /ask /graph /settings
│  ├─ ingest/               # source 별 (url, youtube, github, pdf, arxiv, telegram, slack)
│  ├─ embedding/            # bge-m3 + Qdrant
│  ├─ llm/                  # OpenAI / Anthropic / Ollama provider
│  └─ ...
│
├─ ai_agents/               # multi-channel gateway (Python)
│  ├─ base.py              # ChannelAgent ABC
│  ├─ telegram_inbox_watcher.py
│  ├─ slack_inbox_watcher.py       (Phase 3+)
│  ├─ whatsapp_inbox_watcher.py    (Phase 3+)
│  └─ discord_inbox_watcher.py     (Phase 3+)
│
├─ frontend/app.py          # Streamlit MVP (Settings/Search)
├─ frontend_v2/             # Next.js 14 graph UI (Phase 2.5+)
│
└─ external/                # gitignored 벤치마킹 참조 clone
   ├─ openclaw/             # multi-channel routing UX 참조
   ├─ hermes-agent/         # multi-channel gateway + plugins + auto-skills 참조
   └─ hermes-webui/         # 3 패널 UI + SSE + streaming markdown 참조
```

---

## 3. ChannelAgent ABC (Phase 2.5)

`ai_agents/base.py` 에 정의될 추상 인터페이스. 모든 채널 watcher 가 상속.

```python
# ai_agents/base.py (Phase 2.5 작업)
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator

@dataclass
class ChannelMessage:
    """채널 무관 메시지 표현."""
    channel: str                # "telegram" | "slack" | "discord" | "whatsapp"
    channel_message_id: str     # 채널 고유 ID
    sender: str
    text: str
    timestamp: str              # ISO-8601 UTC
    raw: dict                   # 원본 payload (provenance 보존)

class ChannelAgent(ABC):
    """multi-channel gateway 추상화.

    각 채널 (Telegram/Slack/Discord/WhatsApp) 의 daemon 이 이 ABC 를 상속.
    backend HTTP API (/ingest) 만 호출 — backend LLMProvider 직접 호출 금지
    (§3 책임 분리).
    """
    name: str  # e.g., "telegram"

    @abstractmethod
    async def setup(self) -> None:
        """auth, session 복구, 채널 join 등."""

    @abstractmethod
    async def listen(self) -> AsyncIterator[ChannelMessage]:
        """채널에서 새 메시지 stream. backfill 도 같은 stream 으로."""

    async def on_message(self, msg: ChannelMessage) -> None:
        """메시지 도착 시 처리. backend /ingest 호출 + 성공 시 채널에서 삭제 (inbox 패턴)."""
        ...  # 공통 구현 (URL 추출 → /ingest/auto, 노트 → /ingest/telegram 등)
```

### 현 telegram watcher 의 리팩토링

`ai_agents/telegram_inbox_watcher.py` 가 이 ABC 상속하도록 변경:
- 기존 코드 동작 유지 (Telethon daemon, `--daemon` / `--backfill` / `--restart`)
- ChannelAgent 의 `setup` / `listen` / `on_message` 인터페이스 맞춤
- URL 추출 + `/ingest/auto` 호출 + 성공 시 메시지 삭제 (inbox 패턴) 는 `on_message` 의 공통 구현으로

### 향후 채널 (Phase 3+)

새 채널 추가 = `ChannelAgent` 상속 + `setup` / `listen` 구현 + 진입점 추가. 단계적 추가:
1. **Slack** — `slack_sdk` 의 socket mode 또는 events API. 사용자가 Slack 다시 쓰게 되면.
2. **WhatsApp** — `whatsapp-web.js` (unofficial) 또는 Business API.
3. **Discord** — `discord.py` bot.
4. **Telegram channels** (현 inbox 외 다른 채널, group 등) — Telethon 확장.

각 채널의 token/credential 은 `env/dev.env` 에 (NEVER §7).

---

## 4. 외부 reference 정책

### 라이센스 호환 확인 (2026-05-18)

| 프로젝트 | License | AGPL v3 호환 | vendor 가능 |
|---|---|---|---|
| openclaw | MIT | ✅ | ✅ (attribution 보존) |
| hermes-agent | MIT | ✅ | ✅ (attribution 보존) |
| hermes-webui | MIT | ✅ | ✅ (attribution 보존) |

MIT → AGPL 호환. LinkMind 가 AGPL v3 로 OSS 공개될 때 (Phase 6-B), vendor 한 MIT 코드의
LICENSE/copyright notice 만 보존하면 됨. 다운스트림 통합본은 AGPL.

### vendor 규칙 (NEVER §11 + 본 문서)

- ⚠️ **MIT/Apache 코드 vendor 시 필수**:
  - LICENSE 파일 보존 또는 `THIRD_PARTY_NOTICES.md` 에 attribution 통합
  - copyright notice 보존
  - 파일 상단 출처 주석: `# Adapted from <repo>/<file> (MIT) — Copyright (c) <year> <owner>`
- ❌ **license 호환 안 되는 외부 코드 vendor 절대 금지** (BSL, proprietary 등)
- ⚠️ **GPL → AGPL 은 호환** (둘 다 카피레프트) — 단 그 부분도 AGPL 강제
- ❌ **external/ 의 clone 자체는 항상 gitignored 이고 언제든 삭제 가능** — vendor 한 코드는 LinkMind repo 안에 **복사** 해서 자족적으로 동작하게. `from external.hermes_agent...` 같은 source 참조 import 절대 X. `git submodule` 도 안 씀 (업스트림 추적 부담).
- ❌ **라이센스 우회를 위해 함수명/변수명만 바꾸는 행위 금지** — 법적으로 derivative work 인정됨 (cosmetic 변형 = 우회 불가) + MIT 는 attribution 만으로 완전 자유라 우회 자체가 불필요 + GitHub code search / commit pattern 으로 출처 추적 가능 → 적발 시 평판 회복 불가. **정직한 attribution 이 합법 + 안전 + 평판의 유일한 길**.

### 차용 vs 재작성 판단

| 상황 | 권장 |
|---|---|
| 복잡한 알고리즘 / 검증된 코드 (예: SSE streaming, markdown rendering) | **vendor 일부 모듈** (attribution 보존) |
| 패턴/구조만 필요 (예: ChannelAgent ABC) | **자체 구현** (외부 코드 안 보고 우리 스타일로) |
| UX/디자인 (CSS, layout) | **재구현** (Tailwind 등 우리 스택으로 옮기는 게 더 간단) |
| 통째 fork | **비권장** — 의존성/유지보수 부담. 진짜 hermes-webui 그대로 쓰고 싶으면 그건 LinkMind 가 아니라 hermes-webui 사용 |

---

## 5. 흡수 plan (Phase 2.5 - 3+)

| 패턴 | 출처 | 어디로 흡수 | 방식 | Phase |
|---|---|---|---|---|
| Multi-channel gateway | hermes-agent `gateway/` | `ai_agents/base.py` ChannelAgent ABC | 패턴 (자체 구현) | 2.5 |
| Channel-specific daemon | hermes-agent gateway 각 channel | `ai_agents/{slack,whatsapp,discord}_inbox_watcher.py` | 일부 코드 vendor 가능 (auth/session 핸들링) | 3+ |
| Plugins 아키텍처 | hermes-agent `plugins/` | `backend/ingest/` 정리 (auto dispatcher) | 패턴만 (ABC 강제 X) | 2.5 |
| Auto-skills (자가학습) | hermes-agent `skills/` | `backend/jobs/auto_skill_*.py` (가칭) | 패턴 + 부분 코드 vendor 가능 | 3+ |
| 3 패널 layout (sidebar + main + details) | hermes-webui | `frontend_v2/` Next.js 페이지 | UX 패턴 재구현 (Tailwind) | 2.5 |
| SSE streaming chat | hermes-webui `static/messages.js` | `frontend_v2/components/Ask.tsx` | 패턴 재구현 (Server Sent Events + EventSource 표준) | 2.5 |
| Streaming markdown rendering | hermes-webui | `frontend_v2/` (react-markdown + remark) | 표준 라이브러리 사용 | 2.5 |
| 9 skin / 다크모드 | hermes-webui CSS | `frontend_v2/` Tailwind | 재구현 (Tailwind dark mode) | 2.5+ (POC 후) |
| Onboard daemon 등록 (launchd/systemd) | openclaw `openclaw onboard` | `scripts/install_*_agent.sh` (가칭) | 패턴 차용 | 3+ |

---

## 6. backend HTTP API contract (변화 없음)

`ai_agents/` 모듈이 backend 와 통신하는 인터페이스. 외부 client (openclaw, n8n 등) 가
LinkMind 와 통신할 때도 동일.

### `POST /ingest/auto`
URL host 자동 분류 (youtube → youtube, github → github, .pdf → pdf, 나머지 → url).

### `POST /ingest/telegram`
URL 없는 텔레그램 메시지 = note 저장 (source_type='telegram').

### `POST /ingest/{url,youtube,github,pdf,arxiv,slack}`
명시적 source 별 ingest.

### `POST /search`
semantic + tag 검색. 응답에 topic 정보 포함.

### `POST /ask`
RAG 답변. 향후 SSE streaming 변형 추가 (Phase 2.5+).

### `GET /graph/topics` / `/graph/search` / `/graph/item/{id}` (Phase 2.5 신설)
cytoscape JSON 포맷 — frontend_v2 graph UI 용.

---

## 7. 마이그레이션 노트 (2026-05-18)

이 문서는 `docs/openclaw_integration.md` 를 대체한다. 변경 사유:

- **§3 재정의**: "LinkMind ↔ OpenClaw 두 시스템" 가정 폐기 → "단일 self-contained 시스템"
- **`ai_agents/` 위상 격상**: 단순 inbox watcher 모음에서 multi-channel gateway framework 로
- **외부 reference 정책 명확화**: MIT 호환 시 vendor 가능 (attribution 보존)
- **frontend 결정**: Streamlit (MVP 유지) + Next.js (Phase 2.5+ graph UI 부터, SaaS path 일관)

옛 openclaw_integration.md 의 일부 내용 (HTTP API contract, install_openclaw.sh 사용법)
은 이 문서로 통합됐다. `scripts/install_openclaw.sh` 자체는 선택적 유틸리티로 유지 — 사용자가
별도 openclaw 를 띄우고 싶으면 가능 (LinkMind 의 client 로 동작 가능).
