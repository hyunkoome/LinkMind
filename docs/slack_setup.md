# Slack 인증 자료 추출 & Export 가이드

LinkMind 가 Slack 워크스페이스를 ingest 하려면 두 가지 단계가 필요하다:

1. **인증 자료 확보** — Bot Token 또는 User Token(xoxc) + d 쿠키
2. **데이터 export** — 공식 export (공개 채널만) 또는 slackdump (비공개+DM+첨부 포함)

이 문서는 그 절차와, 환경변수에 어떻게 보관할지 정리한다.

> 인증 자료는 워크스페이스 전체 메시지에 대한 read 권한과 동등하므로 **절대 git 에 커밋 금지**. `env/dev.env` 와 `external/slack/` 는 `.gitignore` 에 이미 포함돼 있다.

---

## 1. 어떤 방법을 써야 하나?

| 시나리오 | 권장 방식 | 이유 |
|---|---|---|
| 공개 채널만 있고 admin 권한 있음 | 공식 Slack Export | 공식 지원, 압축 ZIP, 안정 |
| 비공개 채널이 대부분 / DM 도 포함하고 싶음 | **slackdump (xoxc + d cookie)** | 공식 export 는 Business+ 전용. slackdump 는 free plan 도 OK |
| Slack 앱 만들 권한이 있고 새 메시지 실시간 listen 원함 | Bot Token (xoxb) + Signing Secret | Webhook / Events API 정식 경로 |

대부분의 개인 워크스페이스 = **slackdump 경로** 가 현실적. 아래 §2 부터.

---

## 2. xoxc 토큰 추출 (User Token)

브라우저에서 추출. 매우 쉽지만 **DevTools 의 Console** 에 익숙해야 한다.

### 2.1. Slack 웹 로그인

Chrome 또는 Firefox 로:

```
https://<your-workspace>.slack.com
```

본인 워크스페이스에 평소처럼 로그인. SSO 면 SSO 완료까지.

### 2.2. DevTools 열기

세 가지 방법 중 아무거나:

- **`Ctrl + Shift + I`** (가장 안정적, F12 가 막힌 환경에서도 작동)
- **`Ctrl + Shift + J`** — Console 탭으로 바로 열림
- 페이지 우클릭 → **검사 (Inspect)**

### 2.3. Console 에서 토큰 추출

DevTools 상단 탭 중 **Console** 클릭. 패널 **맨 아래 `>` 옆 입력란** 에 아래 한 줄 입력 후 엔터:

```javascript
JSON.parse(localStorage.localConfig_v2).teams
```

> Chrome 이 처음 paste 시 빨간 경고 (`allow pasting`) 를 띄울 수 있다. 그러면 콘솔에 정확히 `allow pasting` 만 **직접 타이핑** 후 엔터 → 그 다음에야 paste 허용.

결과로 워크스페이스 객체가 펼쳐진다. 본인 워크스페이스의 **`token`** 필드 값을 우클릭 → **Copy string contents** 로 복사. **`xoxc-...`** 로 시작한다.

여러 워크스페이스가 등록돼 있어 보기 번거로우면 한 줄 추가로 간결하게:

```javascript
Object.values(JSON.parse(localStorage.localConfig_v2).teams).map(t => ({name: t.name, domain: t.domain, token: t.token}))
```

---

## 3. d 쿠키 추출 (xoxd-...)

xoxc 토큰만으로는 Slack API 호출이 거부된다. **반드시 d 쿠키와 한 쌍**으로 써야 한다.

1. DevTools 상단 탭 중 **Application** (Firefox 는 **Storage**)
2. 왼쪽 트리 펼치기:
   ```
   Cookies
     └─ https://<your-workspace>.slack.com   (또는 https://slack.com)
   ```
3. 가운데 표에서 Name 컬럼이 정확히 **`d`** 인 행 클릭 (`d-s` 가 아니라 `d`)
4. 아래쪽 상세창 (또는 Value 셀 더블클릭) → 전체 선택 → 복사
5. 값은 **`xoxd-`** 로 시작하고 보통 `%2F`, `%2B`, `%3D` 같은 URL-encoded 문자를 포함. 그 형태 그대로 복사.

> Value 가 매우 길어 셀에서 잘려 보일 수 있다. 셀을 더블클릭하거나 상세창에서 전체 보고 복사할 것.

---

## 4. 추출한 값을 어디에 둘 것인가

### 4.1. LinkMind 환경변수 (`env/dev.env`)

`env/dev.env` 의 Slack 섹션에 아래 키들을 채운다 (모두 `.gitignore` 로 보호됨):

```bash
SLACK_TEAM_ID=T06PXGA7LE7                            # localConfig_v2.teams 의 id 값
SLACK_WORKSPACE_NAME=<워크스페이스 이름>
SLACK_WORKSPACE_DOMAIN=<...>.slack.com 의 서브도메인
SLACK_WORKSPACE_URL=https://<서브도메인>.slack.com/

SLACK_USER_TOKEN=xoxc-...                            # §2 에서 복사한 값
SLACK_D_COOKIE=xoxd-...                              # §3 에서 복사한 값 (URL-encoded 그대로)
```

LinkMind 백엔드(Phase 2)가 Slack Web API 호출 — `conversations.list`, `users.info`, `files.info` 등 — 을 할 때 이 두 값을 사용한다.

### 4.2. slackdump 캐시 (자체 보관)

slackdump 는 자체적으로 `~/.cache/slackdump/<alias>.bin` 에 암호화된 형태로 저장한다. 별도 환경변수 안 봄. 한 번 등록하면 끝:

```bash
slackdump workspace new \
    -token  "$(grep -oE 'xoxc-[^=]+' <<< "$SLACK_USER_TOKEN")" \
    -cookie "$SLACK_D_COOKIE" \
    hkkim                                      # 본인이 부를 alias (예: hkkim)

slackdump workspace list                       # '=> hkkim' 으로 current 확인
```

### 4.3. 임시 보관용 외부 파일 (선택)

작업 편의상 추출 직후 텍스트 파일에 잠시 저장하고 싶다면:

```
external/slack/copy_object   ← Console 의 'Copy object' 결과
external/slack/d_cookie      ← Application 탭 d 쿠키 값 한 줄
```

`external/` 은 이미 `.gitignore` 에 들어있어 안전. **단, 자동 백업 도구가 이 경로를 sync 하지는 않는지 본인 환경 확인 권장.**

---

## 5. 공식 Slack Export 의 xoxe 파일 토큰 (대부분의 경우 불필요)

이건 위와 별개. **공식 Slack Export 페이지** 에서 export 를 만들면 페이지 하단에 "내보내기 파일 다운로드 토큰" 으로 `xoxe-...` 한 줄이 표시된다.

- 용도: **공식 export ZIP 안의** messages.json 에 들어있는 비공개 파일 URL 을 다운로드할 때 `?t=<xoxe>` 로 붙임
- 유효 기간: 사용자가 "토큰 철회" 누르기 전까지

### 우리 케이스에선 사실상 불필요

xoxc + d 쿠키 (§2-3) 만 있으면 Slack Web API 의 모든 호출이 가능하고, 첨부 파일 (`url_private_download`) 도 그 인증으로 다운로드된다. **slackdump 의 `export -files=true` 가 이미 xoxc+cookie 로 첨부를 받아 디렉토리에 넣어준다.** xoxe 는 다음 시나리오에서만 의미:

- 누가 공식 export ZIP **만** 넘겨주고 xoxc/쿠키는 안 줄 때
- xoxc 가 만료됐는데 공식 export 토큰만 살아있을 때

개인 워크스페이스 single-user 사용에서는 **`SLACK_EXPORT_FILE_TOKEN=` 비워두면 됨**.

> Slack UI 가 `xoxe-...-...-...-...` 처럼 토큰을 ellipsis 로 잘라 표시할 수 있어서, 단순 복사로는 진짜 값이 안 얻어진다. 정 필요하면 DevTools Console 에서:
> ```javascript
> [...document.querySelectorAll('input, code, span, td')].map(e => e.value || e.innerText).filter(v => /^xoxe-/.test(v))
> ```
> 로 전체 값 추출.

---

## 6. slackdump Export 실행

(슈수 명령 — 상세 옵션은 [docs/openclaw_integration.md](openclaw_integration.md) 와 무관하게 slackdump 자체 문서.)

### 6.1. 테스트 (한 채널만, 1분)

```bash
# 채널 ID 확인
slackdump list channels | grep -i '<채널이름>'

# 그 ID 로 export
slackdump export \
    -type standard \
    -files=true \
    -o archive/slack_export/_test \
    C06QLDC2G72        # 채널 ID 예시
```

성공하면 `archive/slack_export/_test/<채널이름>/<날짜>.json` 형태로 결과 확인 가능.

### 6.2. 전체 export

```bash
slackdump export \
    -workspace hkkim \
    -type standard \
    -files=true \
    -o archive/slack_export/full_$(date +%Y-%m-%d) \
    -v 2>&1 | tee archive/slack_export/full_$(date +%Y-%m-%d).log
```

- `-type standard`: Slack 공식 export 와 같은 디렉토리 구조 (`<channel>/<yyyy-mm-dd>.json` + `attachments/`)
- `-files=true`: 첨부 파일까지 함께 다운로드 (raw-first 원칙상 필수)
- 183 채널 + DM + 첨부 기준 30 분 ~ 2 시간 소요

중단 시: slackdump 4.x 의 `archive` 명령은 SQLite + `resume` 지원. 큰 워크스페이스라 안정성 우선이면:

```bash
slackdump archive -workspace hkkim -o archive/slack_export/_chunks
# 중단 시:
slackdump resume archive/slack_export/_chunks
# 완료 후 standard 포맷으로 변환:
slackdump convert -type standard -o archive/slack_export/full archive/slack_export/_chunks
```

---

## 7. 보안 체크리스트

- [ ] `env/dev.env`, `external/slack/`, `archive/slack_export/` 모두 `.gitignore` 에 포함돼 있는지 확인
  ```bash
  git check-ignore env/dev.env external/slack/copy_object archive/slack_export/full_2026-05-14
  # 모두 출력되면 OK
  ```
- [ ] commit 전에 항상 `git status` 확인 — 추적되지 않는 파일에 토큰이 새지 않았는지
- [ ] Phase 2 의 ingest 가 끝나면 Slack 페이지에서 **xoxe 토큰 철회**
- [ ] xoxc 토큰은 Slack 로그아웃 / 세션 만료와 함께 무효화됨 (영구 토큰 아님). 만료되면 §2~§3 재추출

---

## 8. 자주 발생하는 문제

| 증상 | 원인 / 조치 |
|---|---|
| `slackdump workspace new` 가 "no such workspace" | URL 대신 단순 alias (예: `hkkim`) 로 시도 |
| `del` 명령이 멈춤 | `rm ~/.cache/slackdump/<alias>.bin ~/.cache/slackdump/workspace.txt` 후 재등록 |
| 첨부 일부가 403 | 비공개 파일 URL — xoxe 토큰을 `-export-token` 으로 추가 |
| Console paste 거부 (`Self-XSS`) | 콘솔에 `allow pasting` 만 직접 타이핑 후 엔터 |
| 비공개 채널이 안 보임 | 본인이 그 채널의 멤버인지 Slack UI 에서 확인. 멤버 아닌 채널은 어차피 read 권한 없음 |
| 결과 디렉토리가 비어있음 | `-time-from` 옵션이 너무 최근이거나, 채널 자체에 메시지가 없을 수 있음 |

---

## 9. 관련 파일 / 모듈

- 환경변수 정의: [env/dev.env.example](../env/dev.env.example) (실제 값은 `env/dev.env`)
- 백엔드 설정 로더: [backend/config.py](../backend/config.py)
- ✅ Slack export 파서: [backend/ingest/slack/export_parser.py](../backend/ingest/slack/export_parser.py) — slackdump standard 포맷 → SlackMessage iterator (Phase C wave-2, 2026-05-19)
- ✅ Slack ingest 진입점: [backend/ingest/slack/__init__.py](../backend/ingest/slack/__init__.py) — `ingest_slack_message` (단일) + `ingest_slack_export` (폴더)
- ✅ Slack ingest CLI: [backend/ingest/slack/__main__.py](../backend/ingest/slack/__main__.py) — `python -m backend.ingest.slack`
- ✅ 전체 backfill 스크립트: [scripts/slack_ingest_all.sh](../scripts/slack_ingest_all.sh) — 사전 점검 + tqdm 진행률 + 이슈 manifest 자동
- ⏸ Slack Web API 클라이언트 (Phase 3+): `backend/ingest/slack/api_client.py` — 사용자 구독 해제 예정이라 보류
- 데이터 설계: [docs/training_data_design.md](training_data_design.md)

---

## 10. Slack ingest — slackdump export → LinkMind backfill

slackdump 로 받은 export 디렉토리를 LinkMind item 으로 통째 backfill (Phase C
wave-2, 2026-05-19~). Telegram 패턴 일관.

```bash
# 1) 사전 — backend uvicorn 종료 (bge-m3 GPU 점유 해제, OOM 회피)
bash scripts/step5_run_dev.sh --stop

# 2) 전체 워크스페이스 backfill
bash scripts/slack_ingest_all.sh

# 또는 옵션
bash scripts/slack_ingest_all.sh --channel 가-공부-cuda-programming  # 단일 채널 (디버깅)
bash scripts/slack_ingest_all.sh --force                            # 동일 hash 도 summary/tags 재계산
.venv/bin/python -m backend.ingest.slack archive/slack_export/latest \
    --workspace-url https://w1710672365-sjj477000.slack.com           # CLI 직접

# 3) 완료 후 재기동
bash scripts/step5_run_dev.sh
```

### 동작

각 Slack 메시지 (시스템 메시지 / 빈 메시지 skip) 에 대해:

1. **URL 자동 라우팅** — text + blocks 의 URL 추출, mrkdwn entity 정리
   (`<url|label>` → label / `<@U>` 제거 / `<#C|name>` → `#name` / `<!here>` →
   `@here`). 각 URL 은 host 별 분기 (ingest_url / ingest_youtube / ingest_github
   / ingest_pdf).
2. **첨부 ingest** — `attachments/<file_id>-<name>` 로컬 파일을 `ingest_document`
   (PDF/DOCX/PPTX/TXT/MD 통합 추출) 로.
3. **caption 정책** (= `items.user_notes`):
   - thread 자식 → **부모 메시지 cleaned text** (논문 제목 같은 묶음 라벨)
   - 첨부 있고 본문 → 본문 그대로
   - URL + 메모 → URL 제거한 나머지 (`_strip_urls_for_caption`)
4. **URL/첨부 없고 cleaned text >= 20자** → `source_type='slack'` note. `source_
   metadata['slack']` 에 ts/channel/channel_id/user/thread_ts/permalink 보존.

### 이슈 manifest

DB 에 안 들어간 자료 (LinkedIn login wall / 정적 project page / mp4 video /
단축 URL 등) 는 `archive/slack_export/issues/<timestamp>/manifest.json` 에 자동
보존 — 후속 wave 에서 패턴별 별도 처리. 정책 (2026-05-19): manifest 는 항상
`archive/slack_export/` 하위에만 (/tmp 휘발성 위치 금지).

manifest entry 구조:

```json
{
  "ts": "1726737542.090369",
  "channel": "가-공부-논문쓰기-image-composition-이미지-물체-추가",
  "permalink": "https://<workspace>.slack.com/archives/.../p...",
  "url": "https://www.linkedin.com/posts/...",
  "kind": "url",
  "issue": "placeholder",
  "error": null,
  "raw_len": 232,
  "chunks_indexed": 0
}
```

분석 예:

```bash
# 도메인별 카운트
jq '.[] | .url' archive/slack_export/issues/<ts>/manifest.json \
    | sed 's|/[^/]*$||' | sort | uniq -c | sort -rn

# 특정 issue 유형만
jq '.[] | select(.issue == "placeholder") | .url' .../manifest.json
```

### GPU OOM 회피 (중요)

vLLM 컨테이너 (qwen2.5-7B, ~18 GB) + backend uvicorn 의 bge-m3 (~3.78 GB) 가
24 GB GPU 거의 점유. CLI ingest 가 별도 프로세스라 bge-m3 를 또 로드하려다
OutOfMemoryError. **ingest 도중에는 uvicorn 종료 필수**:

```bash
bash scripts/step5_run_dev.sh --stop
bash scripts/slack_ingest_all.sh
bash scripts/step5_run_dev.sh    # 끝나면 재기동
```

장기 fix 후보 (wave-5+): bge-m3 를 **TEI (Text Embedding Inference) 컨테이너** 로
분리 → 모든 프로세스가 같은 HTTP 서버 호출 → 모델 1번만 GPU. CLAUDE.md §12 의
Phase 2 backlog 항목.
