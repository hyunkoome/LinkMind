# `ai_agents/` — LinkMind 의 client agent

LinkMind 자체는 HTTP API (`/ingest`, `/search`, `/ask`, `/topics`) 만 노출하는
backend knowledge OS. 사용자가 텔레그램/슬랙/디스코드 등에서 LinkMind 와 상호작용
하려면 그 사이를 잇는 **client agent** 가 필요. 이 폴더가 그 agent 들이 사는 곳.

## 왜 backend 가 아니라 별 폴더?

`CLAUDE.md §3` NEVER 목록:
> ❌ Telegram/Slack 봇을 LinkMind 안에 직접 만들기 (OpenClaw 위임이 기본)

즉 봇/agent 코드는 LinkMind backend 의 일부가 아님. backend 는 도구 (HTTP API)
일 뿐이고, agent 가 그 도구를 호출. backend 가 깨지거나 deploy 환경이 바뀌어도
agent 는 영향 X — 또는 agent 가 OpenClaw / Discord bot / Claude Desktop 같이
다른 client 로 교체돼도 backend 는 그대로.

`scripts/` 는 운영/셋업 셸 스크립트 모음 (stepN_*.sh 등) — agent 와 의미가
다름. `ai_agents/` 로 분리해서 "LinkMind 외부에서 LinkMind 를 호출하는 daemon"
임을 명확히.

## 현재 agent

### `telegram_inbox_watcher.py` + `.sh`

LinkMind-Inbox 텔레그램 채널의 메시지를 받아 LinkMind 로 자동 ingest:
- URL 던지면 host 별 ingester (youtube/github/pdf/url) 라우팅 + topic 매핑
- URL 없는 텍스트는 `source_type='telegram'` note 로 저장
- ingest 성공 시 채널의 메시지 **자동 삭제** (inbox 패턴 — 처리 안 된 것만 남음)

셋업/사용: `docs/telegram_setup.md` 참조. 첫 실행 시 SMS 인증 후 session 자동 저장,
다음부터는 `bash scripts/step5_run_dev.sh --daemon` 이 backend/frontend 와 함께
자동 가동.

## 새 agent 추가 시

향후 추가 가능 (각각 별 process daemon):
- `discord_inbox_watcher.py` — Discord 채널 inbox
- `slack_realtime_watcher.py` — Slack Real Time Messaging
- `openclaw_bridge.py` — OpenClaw 의 telegram extension 인계
- `imap_watcher.py` — 이메일 forward 로 자료 모으기

규칙:
1. backend 모듈 import 는 OK (`from backend.ingest.telegram import ...`).
   다만 backend 의 일부가 아니라 **소비자** — backend 가 안정적이라는 가정.
2. agent 자체의 의존성은 `requirements.txt` 의 별 그룹 (예: `telethon` 은
   watcher 만 필요). 무거운 client SDK 가 backend 의존성에 들어가지 않게.
3. 환경변수는 `env/dev.env` 의 agent 별 prefix (`TELEGRAM_*`, `DISCORD_*` 등).
   미설정 시 `step5_run_dev.sh` 가 friendly skip.
4. 셋업 가이드는 `docs/<agent>_setup.md`.
