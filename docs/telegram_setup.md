# Telegram inbox 셋업 (Phase C wave-1)

LinkMind 의 자료 유입 경로 중 하나. 사용자가 텔레그램 채널 (예: `LinkMind-Inbox`)
에 URL/텍스트를 던지면 watcher 가 받아 LinkMind 로 자동 ingest.

**CLAUDE.md §3 NEVER 규정 준수**: LinkMind backend 안에 봇 코드를 두지 않음.
실시간 수신은 `scripts/telegram_inbox_watcher.py` (별 process daemon) 가 담당하고,
backend.ingest.telegram 모듈은 단순 파서 + ingest helper 만 노출.

## 1. API ID/Hash 발급 (한 번)

1. https://my.telegram.org → 텔레그램 계정 로그인 (SMS 코드)
2. "API development tools" → "Create new application"
3. 폼 권장 값:

| 필드 | 권장 |
|---|---|
| App title | 자유 (예: `hkkim_telegram_ai`) |
| Short name | 5-32자 알파넘릭/`_` (예: `linkmind_inbox`) |
| URL | 비워둠 |
| Platform | **Desktop** |
| Description | `Personal LinkMind inbox watcher` |

4. 발급 결과의 `api_id` (정수) 와 `api_hash` (32자) 를 `env/dev.env` 에:

```bash
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=abcdef0123456789abcdef0123456789
TELEGRAM_SESSION_PATH=volumes/telegram/inbox.session
TELEGRAM_INBOX_INVITE=https://t.me/+2ztAGOP93_Q3NzQ1
```

5. `pip install -r requirements.txt` — `telethon>=1.36.0` 이 함께 깔림.

## 2. 첫 실행 — 인증 (한 번)

```bash
bash scripts/telegram_inbox_watcher.sh
```

대화식 단계:
- "Please enter your phone (or bot token):" → 사용자 텔레그램 계정 번호 (`+82...`)
- "Please enter the code you received:" → 텔레그램 앱에 도착한 SMS 코드
- 2FA 켜놨으면 "Please enter your password:" → 그 비밀번호

성공 시 `volumes/telegram/inbox.session` 파일 자동 생성 → **다음부터는 인증 X**.

`Ctrl+C` 로 종료.

## 3. 평소 사용

```bash
# 포어그라운드 (디버그용)
bash scripts/telegram_inbox_watcher.sh

# 채널의 최근 50개 메시지도 함께 처리
bash scripts/telegram_inbox_watcher.sh --backfill 50

# 백그라운드 daemon
bash scripts/telegram_inbox_watcher.sh --daemon
bash scripts/telegram_inbox_watcher.sh --status   # pid 확인
bash scripts/telegram_inbox_watcher.sh --stop

# 로그 보기
tail -f /tmp/telegram-watcher.log
```

## 4. 동작 흐름

watcher 가 새 메시지 도착 시:

```
Telegram 채널
    │ NewMessage event
    ▼
scripts/telegram_inbox_watcher.py
    │ TelegramMessage dataclass
    ▼
backend.ingest.telegram.ingest_telegram_message()
    │
    ├─ 메시지에 URL 있으면 → host 별 ingester 라우팅
    │     youtube.com → ingest_youtube
    │     github.com  → ingest_github
    │     *.pdf       → ingest_pdf
    │     그 외       → ingest_url
    │     → 자동으로 topic 그룹핑까지
    │
    └─ URL 없는 텍스트 (20자 이상) → source_type='telegram' note item
          → external_ids 추출 + auto_link_topics
          → embedding + LLM 요약
```

따라서 사용자가 "이 논문 봐야지" 같이 arxiv URL 한 줄 던지면 자동으로:
1. arxiv abstract 추출 → 한국어 요약 + 해시태그
2. `arxiv:<id>` topic 자동 생성/매칭
3. 이미 그 paper 의 GitHub 가 LinkMind 에 있으면 같은 topic 으로 묶임

## 5. Export 폴더 import (Bonus — 옵션 A 도 지원)

watcher 가 꺼져 있던 동안의 메시지를 일괄 import:

```bash
# Telegram Desktop → 채널 우클릭 → "Export chat history" → JSON 형식 → 폴더 저장
python -m backend.ingest.telegram <export_dir>
python -m backend.ingest.telegram <export_dir> --force   # 재요약
```

`backfill N` 보다 더 옛 메시지가 필요할 때 유용. parser 는 `telegram_export_sample.json`
fixture 로 17 unit tests 검증됨.

## 6. Inbox 패턴 — 처리된 메시지 자동 삭제

`TELEGRAM_DELETE_AFTER_INGEST=true` (기본) 이면 ingest 가 성공한 메시지는 watcher
가 채널에서 자동 삭제. 채널에는 **처리되지 않은 메시지** (LinkMind 가 못 받은 것,
또는 LLM 요약 실패한 것) 만 남아 시각적으로 알 수 있음 — 로컬에서 LinkMind 가 잠시
꺼져있던 동안 던진 메시지 / network glitch 등 추적 비용이 크게 줄어듦.

성공 판단:
- 메시지의 URL 들이 모두 에러 없이 ingest → 삭제
- URL 없이 `source_type='telegram'` note 가 저장됨 → 삭제
- 너무 짧은 메시지 (`< 20자` 와 URL 없음) 또는 ingest 에러 → 보존

끄려면 `env/dev.env` 에:
```bash
TELEGRAM_DELETE_AFTER_INGEST=false
```

## 7. 트러블슈팅

- **invite link 만료/유효하지 않음**: invite link 가 재생성됐으면 `TELEGRAM_INBOX_INVITE` 갱신
- **session 인증 실패**: `volumes/telegram/inbox.session` 삭제 후 재실행 (다시 SMS 인증)
- **`telethon` import 실패**: `.venv` 가 활성화됐는지, `pip install -r requirements.txt` 실행했는지

## 8. 보안 노트

- `TELEGRAM_API_ID/HASH` 와 `*.session` 은 사용자 계정 권한과 동등. 절대 commit 금지
  (`.gitignore` 에 `env/dev.env`, `volumes/` 둘 다 이미 있음)
- watcher daemon 이 외부 호스트로 메시지를 전송하지 않음 — 모든 ingest 는 localhost LinkMind backend 로만
- OpenClaw 가 구축되면 이 daemon 은 OpenClaw 의 telegram extension 으로 교체 가능 — LinkMind backend 코드 변경 없음.
