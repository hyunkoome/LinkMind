# 멀티테넌트 / 그룹 분리 설계 (Multitenant & Groups)

LinkMind 데이터를 **space(조직/개인 공간) 단위로 격리**하고, space *안에서* **group**
으로 정리하기 위한 설계. §14 Privacy #2(tenant isolation) 구현. 기간/일정은 적지 않음 —
진행 *순서* 만.

---

## 1. 통합 모델 — 핵심 통찰

개인 워크스페이스와 조직은 **같은 메커니즘**이다. 두 축이 직교한다:

| 개념 | 역할 | 개인 | 조직 |
|---|---|---|---|
| **space** | 데이터 소유 = **격리(권한) 경계** | 멤버 1명인 space | 멤버 N명인 space |
| **member** | space 접근권 — 멤버는 그 space 데이터 **전부 공유** | 1명(본인) | N명 |
| **group** | space *내부* 정리용 하위 분류 (권한 경계 아님, 뷰/필터) | "그룹 나누기" | (옵션) |

- **권한/격리 = space(멤버십)** — RLS·쿼리·Qdrant 가 `space_id` 로 강제.
- **정리 = group(space 내부)** — nullable `group_id`, 권한 로직 없음. 단순 필터.
- 한 유저는 여러 space 소속 가능. **활성 space** 가 테넌트 컨텍스트(JWT/UI 전환).
- 조직 내 멤버 간 per-row ACL **없음** (멤버는 space 데이터 전부 공유) → 단순.

→ 테넌트 메커니즘 하나만 제대로 만들면 개인/조직이 같은 코드로 동작. group 은 그 위에
얹는 가벼운 분류.

---

## 2. 데이터 모델

신규 테이블:

```sql
users(id uuid pk, email text unique, password_hash text, created_at timestamptz)
spaces(id uuid pk, name text, kind text default 'personal', created_at)   -- kind: personal|org
space_members(space_id uuid fk, user_id uuid fk, role text, created_at,
              primary key (space_id, user_id))                            -- role: owner|admin|member
groups(id uuid pk, space_id uuid fk, name text, created_at)               -- space 내부 분류
```

기존 테넌트-스코프 테이블에 컬럼 추가:

```sql
-- 격리 경계 (NOT NULL, FK) + 정리용 (nullable)
ALTER TABLE items       ADD COLUMN space_id uuid NOT NULL REFERENCES spaces(id),
                        ADD COLUMN group_id uuid REFERENCES groups(id);
ALTER TABLE wiki_pages  ADD COLUMN space_id uuid NOT NULL REFERENCES spaces(id),
                        ADD COLUMN group_id uuid REFERENCES groups(id);
ALTER TABLE topics      ADD COLUMN space_id uuid NOT NULL REFERENCES spaces(id);
ALTER TABLE attachments ADD COLUMN space_id uuid NOT NULL REFERENCES spaces(id);
ALTER TABLE agent_runs  ADD COLUMN space_id uuid;  -- 로그성, nullable 허용 검토
-- junction(item_topics, wiki_page_items): 부모(item/wiki)의 space 를 따름.
--   RLS 편의상 space_id 를 비정규화 복제할지 결정 (조인 비용 vs 단순 정책).
-- app_settings: 전역 vs space별 — vLLM/프롬프트는 전역 유지, 일부는 space별 검토.
```

UNIQUE 제약 갱신: `items` 의 `UNIQUE(source_type, raw_content_hash)` →
**`UNIQUE(space_id, source_type, raw_content_hash)`** (같은 자료라도 space 별 별개).

---

## 3. 격리 메커니즘 — 2중 방어 (cross-space 유출 = NEVER)

1. **앱 레벨 필터**: 모든 read/write 쿼리가 `current_space_id` 로 필터. repository 계층에
   space 주입(누락 방지를 위해 공통 헬퍼/베이스 쿼리로).
2. **DB 레벨 RLS (안전망)**: Postgres Row Level Security. 요청마다
   `SET LOCAL app.current_space_id = '<uuid>'` → 각 테이블 정책
   `USING (space_id = current_setting('app.current_space_id')::uuid)`.
   앱 필터를 한 번 빠뜨려도 안 샌다. **이게 §14 #2 의 핵심 보증.**
3. **Qdrant 필터** (자주 빠뜨림 ⚠): 모든 point payload 에 `space_id` 추가, **모든 벡터
   검색**(search / ask / wiki body / classifier 후보 retrieval)에 `Filter(must=[match space_id])`.
   누락 시 벡터 유출.

---

## 4. 인증 & 컨텍스트

- **id/pw 먼저**: `POST /auth/login {email, password}` → bcrypt 검증 → **JWT** 발급
  (payload: user_id, active_space_id). `GET /auth/me`. 초기 유저/space seed.
- **Google OAuth 나중**: 같은 JWT 를 발급하는 *또 하나의 로그인 방식* (재작성 X).
- 의존성: `passlib[bcrypt]` + `pyjwt`(또는 python-jose). JWT secret = `env/dev.env` (`LINKMIND_JWT_SECRET`).
- **요청 흐름**: `Depends(get_current_user)` → JWT 검증 → user + active_space 결정 →
  멤버십 확인 → DB 세션에 `SET LOCAL app.current_space_id` + Qdrant 필터에 주입.
- **활성 space 전환**: `POST /auth/switch-space` (멤버인 space 만). UI 상단 space 선택기.
- `ai_agents`(텔레그램 watcher 등)도 자기 space 컨텍스트로 ingest — service 계정/기본 space 매핑 필요.

---

## 5. 마이그레이션 (기존 데이터)

1. 기본 space 1개 생성(본인 owner) + 본인 user.
2. 기존 모든 행(items/wiki_pages/topics/attachments …)의 `space_id` = 기본 space 백필.
3. **Qdrant**: 기존 모든 point payload 에 `space_id` 백필 (linkmind_items + linkmind_wiki_pages).
4. UNIQUE 제약 교체는 백필 후.
5. idempotent 배치 잡 (`backend/jobs/`), dry-run 먼저.

---

## 6. 코드 영향 범위 (고쳐야 할 곳)

- **backend/db**: schema.sql, repository.py (모든 쿼리 space 필터), connection (요청별 RLS 변수 set).
- **backend/api**: 모든 라우터에 `Depends(current_user/current_space)` + 신규 `auth.py`.
- **backend/ingest/***: ingest 시 space_id 부여 (요청 컨텍스트 또는 채널→space 매핑).
- **backend/agents** (classifier/retriever/writer): 후보 retrieval·wiki 생성에 space 스코프.
- **backend/embedding** (qdrant_store, wiki_qdrant): upsert payload + search filter 에 space_id.
- **frontend**: 로그인 페이지, `fetchJSON` 에 Authorization 헤더, 401 redirect, space 선택기, group 필터 UI.
- **jobs**: 마이그레이션 잡 + 기존 잡들(backfill/cleanup)에 space 인지.

---

## 7. 빌드 단계 (순서)

1. **인증 + space 토대** — users·spaces·space_members 테이블, id/pw 로그인, JWT(활성 space),
   `get_current_user`/`current_space` Depends, 전 API 보호, 로그인 UI. 기본 space seed.
2. **space_id 스코핑 + RLS + Qdrant 필터** — 컬럼 추가 + 마이그레이션 백필 + 모든 쿼리/검색
   필터 + RLS 정책. **격리 핵심.**
3. **group** — groups 테이블 + nullable group_id + UI 분류 필터 (개인 워크스페이스 "그룹 나누기").
4. **조직 멤버 초대** 흐름 (멤버 N명 space).
5. **Google OAuth** (로그인 방식 추가).

---

## 8. 테스트 (필수)

- **cross-space 격리**: space A 유저가 space B 의 item/wiki/검색결과/Qdrant hit 를 **절대 못 봄**.
  RLS 정책 + 앱 필터 + Qdrant 필터 각각, 그리고 "필터 일부러 빼도 RLS 가 막는지" 검증.
- 인증: 무토큰/만료/타 space 토큰 → 401/403.
- 마이그레이션: 백필 후 모든 행 space_id NOT NULL, Qdrant payload 존재.

---

## 9. 미결정 / 주의

- junction 테이블 RLS: space_id 비정규화 복제 vs 부모 조인 — 정책 단순성 위해 복제 유력.
- `app_settings`(vLLM/프롬프트): 전역 유지 vs space별. 우선 전역, 나중 space별 override 검토.
- 텔레그램/Slack watcher 의 space 귀속(서비스 계정 ↔ space 매핑).
- 토큰 저장: localStorage(간단) vs httpOnly 쿠키(XSS 안전, CSRF 설정 필요) — Phase 1 에서 결정.
- 개인 워크스페이스의 group 과 (옛) ChatGPT식 ask "프로젝트"(localStorage) 통합 여부.
