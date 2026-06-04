// backend (FastAPI :8000) 호출 wrapper.
// CORS 가 backend 에 enabled (allow_origins=["*"]) 라 별도 proxy 불필요.

import type {
  GraphResponse,
  ItemDetail,
  ItemUpdateRequest,
  LLMSettings,
  LLMSettingsUpdate,
  ModelsListResponse,
  PromptVersion,
  UrlIngestRequest,
  UrlIngestResponse,
} from "@/types/graph";

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";

async function fetchJSON<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    // 멀티테넌트(2026-06-03): JWT 는 httpOnly 쿠키 → 모든 요청에 쿠키 동봉.
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
  });
  // 세션 만료/미인증 → 로그인 페이지로. 단 /auth/* 호출(로그인 폼·me 확인)은 제외 —
  // 폼이 직접 에러를 표시하거나 AuthProvider 가 null 처리한다.
  if (
    res.status === 401 &&
    typeof window !== "undefined" &&
    !path.startsWith("/auth/") &&
    window.location.pathname !== "/login"
  ) {
    window.location.href = "/login";
  }
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  return res.json() as Promise<T>;
}

// D10.5 세션 B — keyword ▸ wiki ▸ item 그래프 (옛 category/topic 폐기).
// 상위 keyword 그룹 노드 (빈도순). 시작 화면 — 가벼움.
export async function getGraphKeywords(limit = 100000): Promise<GraphResponse> {
  return fetchJSON<GraphResponse>(`/graph/keywords?limit=${limit}`);
}

// keyword 클릭 시 expand — 그 keyword 의 wiki 들 + 각 wiki 의 item.
export async function expandGraphKeyword(keyword: string): Promise<GraphResponse> {
  return fetchJSON<GraphResponse>(`/graph/keyword/${encodeURIComponent(keyword)}`);
}

// wiki 클릭 시 expand — 그 wiki 1개 + 그 안의 item (sources/figures).
export async function expandGraphWiki(slug: string): Promise<GraphResponse> {
  return fetchJSON<GraphResponse>(`/graph/wiki/${encodeURIComponent(slug)}`);
}

export async function searchGraph(q: string, limit = 50): Promise<GraphResponse> {
  const params = new URLSearchParams({ q, limit: String(limit) });
  return fetchJSON<GraphResponse>(`/graph/search?${params}`);
}

export async function getItemNeighborhood(itemId: string): Promise<GraphResponse> {
  return fetchJSON<GraphResponse>(`/graph/item/${itemId}`);
}

export async function getItem(itemId: string): Promise<ItemDetail> {
  return fetchJSON<ItemDetail>(`/items/${itemId}`);
}

export async function patchItem(
  itemId: string,
  body: ItemUpdateRequest,
): Promise<ItemDetail> {
  return fetchJSON<ItemDetail>(`/items/${itemId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

// ── Cleanup 페이지 (D12) ────────────────────────────────────────

export interface ItemAttachmentSummary {
  id: string;
  role: string | null;
  mime_type: string | null;
  file_size: number | null;
  file_hash: string;
  caption: string | null;
  width: number | null;
  height: number | null;
}

// 첨부 파일 inline URL (PDF viewer 등) — backend 의 /files/{hash}
export function fileUrl(fileHash: string): string {
  return `${API_BASE}/files/${fileHash}`;
}

// markdown 본문 안의 상대경로 자산(/files/{hash} 등)을 backend 절대 URL 로 변환.
// 위키 body 의 figure 이미지가 '/files/...' 상대경로면 frontend(3001) 로 가서 404 나므로
// API_BASE(8000) 를 붙여준다. 이미 절대 URL(http) 이면 그대로.
export function resolveAssetUrl(url: string): string {
  if (!url) return url;
  if (url.startsWith("/files/")) return `${API_BASE}${url}`;
  return url;
}

// ── Settings ────────────────────────────────────────────────────

export async function getLLMSettings(): Promise<LLMSettings> {
  return fetchJSON<LLMSettings>(`/settings/llm`);
}

export async function updateLLMSettings(body: LLMSettingsUpdate): Promise<LLMSettings> {
  return fetchJSON<LLMSettings>(`/settings/llm`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export async function listModels(): Promise<ModelsListResponse> {
  return fetchJSON<ModelsListResponse>(`/settings/llm/models`);
}

// ── 키워드 정규화 설정 (약어/별칭) ──
export interface KeywordConfig {
  acronyms: string;
  aliases: string;
  defaults: { acronyms: string; aliases: string };
}

export async function getKeywordConfig(): Promise<KeywordConfig> {
  return fetchJSON<KeywordConfig>(`/settings/keywords`);
}

export async function updateKeywordConfig(
  body: { acronyms?: string; aliases?: string },
): Promise<KeywordConfig> {
  return fetchJSON<KeywordConfig>(`/settings/keywords`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export async function reapplyKeywordConfig(): Promise<{
  ok: boolean; total: number; changed: number;
  keywords_before: number; keywords_after: number;
}> {
  return fetchJSON(`/settings/keywords/reapply`, { method: "POST" });
}

export async function listPromptVersions(name: string): Promise<PromptVersion[]> {
  // backend 응답: {name: string, versions: PromptVersion[]} — versions 만 풀어서 반환
  const res = await fetchJSON<{ name: string; versions: PromptVersion[] }>(
    `/settings/prompts/${name}/versions`,
  );
  return res.versions || [];
}

export async function savePromptVersion(
  name: string, body: { content: string; note?: string },
): Promise<PromptVersion> {
  return fetchJSON<PromptVersion>(`/settings/prompts/${name}`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function activatePromptVersion(
  name: string, version: string,
): Promise<PromptVersion> {
  return fetchJSON<PromptVersion>(`/settings/prompts/${name}/activate`, {
    method: "POST",
    body: JSON.stringify({ version }),
  });
}

// ── Ingest ──────────────────────────────────────────────────────

export async function ingestAuto(body: UrlIngestRequest): Promise<UrlIngestResponse> {
  return fetchJSON<UrlIngestResponse>(`/ingest/auto`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function uploadPdf(
  file: File, force = false,
): Promise<UrlIngestResponse> {
  const form = new FormData();
  form.append("file", file);
  const params = new URLSearchParams({
    analyze_now: "true",
    force: force ? "true" : "false",
  });
  const res = await fetch(`${API_BASE}/ingest/pdf/upload?${params}`, {
    method: "POST",
    body: form,
    credentials: "include",
  });
  if (res.status === 401 && typeof window !== "undefined") {
    window.location.href = "/login";
  }
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  return res.json();
}

// ── Ask (대화형 RAG, 2026-05-27) ───────────────────────────────

export interface AskTurn {
  role: "user" | "assistant";
  content: string;
}

export interface AskRequest {
  question: string;
  top_k?: number;
  llm_provider?: string;
  llm_model?: string;
  // ask 에서 URL 붙여 방금 ingest 한 item — context 최상단 강제 포함 (URL-paste-ingest)
  pin_item_ids?: string[];
  // 멀티턴 — 현재 질문 이전의 user/assistant 턴 (오래된→최신). 맥락 유지 + 후속질문 재작성.
  history?: AskTurn[];
}

export interface AskCitation {
  item_id: string;
  title: string | null;
  source_url: string | null;
  snippet: string | null;
}

export interface AskRelatedWiki {
  slug: string;
  title: string;
  description: string | null;
  body_status: string;
  overlap: number;
}

export interface AskResponse {
  question: string;
  answer: string;
  citations: AskCitation[];
  related_wikis: AskRelatedWiki[];
  llm_provider: string;
  llm_model: string;
}

export async function askQuestion(body: AskRequest): Promise<AskResponse> {
  return fetchJSON<AskResponse>(`/ask`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// ── Ask streaming (멀티턴, SSE) ────────────────────────────────
// POST /ask/stream 은 검색이 끝나는 즉시 meta(citations/related_wikis)를 1회 보내고,
// 이어 답변을 token 델타로 흘려보낸다. 우측 위키 패널을 답변보다 먼저 띄울 수 있다.

export interface AskStreamMeta {
  question: string;
  search_query: string;
  citations: AskCitation[];
  related_wikis: AskRelatedWiki[];
  llm_provider: string;
  llm_model: string;
}

export interface AskStreamHandlers {
  onMeta?: (meta: AskStreamMeta) => void;
  onToken?: (text: string) => void;
  onError?: (message: string) => void;
  signal?: AbortSignal; // 진행 중 취소 (세션 전환/언마운트)
}

/**
 * /ask/stream 을 호출해 SSE 프레임을 파싱하며 콜백을 호출한다.
 * 프레임 구분은 "\n\n", 각 프레임의 "data: {json}" 한 줄을 파싱.
 * 네트워크/HTTP 오류는 throw, LLM 생성 중 오류는 onError 로 전달된다.
 */
export async function askQuestionStream(
  body: AskRequest,
  h: AskStreamHandlers,
): Promise<void> {
  const res = await fetch(`${API_BASE}/ask/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal: h.signal,
    credentials: "include",
  });
  if (res.status === 401 && typeof window !== "undefined") {
    window.location.href = "/login";
  }
  if (!res.ok || !res.body) {
    const t = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}: ${t}`);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  // eslint-disable-next-line no-constant-condition
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buf.indexOf("\n\n")) !== -1) {
      const frame = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const line = frame.split("\n").find((l) => l.startsWith("data: "));
      if (!line) continue;
      let evt: { type: string; text?: string; message?: string } & Partial<AskStreamMeta>;
      try {
        evt = JSON.parse(line.slice(6));
      } catch {
        continue;
      }
      if (evt.type === "meta") h.onMeta?.(evt as unknown as AskStreamMeta);
      else if (evt.type === "token") h.onToken?.(evt.text || "");
      else if (evt.type === "error") h.onError?.(evt.message || "unknown error");
      // "done" — 별도 처리 없이 루프 종료를 기다린다.
    }
  }
}

// ── Auth / Multitenant (2026-06-03 단계 A) ─────────────────────
// JWT 는 httpOnly 쿠키 → 토큰을 JS 가 직접 만지지 않는다. 상태는 GET /auth/me 로 복원.

export interface AuthSpace {
  id: string;
  name: string;
  kind: string;
  role: string | null;
}

export interface AuthUser {
  id: string;
  email: string;
  display_name: string | null;
  active_space_id: string;
  spaces: AuthSpace[];
  must_change_password: boolean;
}

// 로그인 — 성공 시 backend 가 Set-Cookie. 실패(401)는 throw (폼에서 표시).
export async function login(email: string, password: string): Promise<AuthUser> {
  return fetchJSON<AuthUser>(`/auth/login`, {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

// 첫 로그인 강제 변경 — 현재 비번 확인 후 새 비번(필수)/이메일(선택). must_change_password 해제.
export async function changeCredentials(
  currentPassword: string,
  newPassword: string,
  newEmail?: string,
): Promise<AuthUser> {
  return fetchJSON<AuthUser>(`/auth/change-credentials`, {
    method: "POST",
    body: JSON.stringify({
      current_password: currentPassword,
      new_password: newPassword,
      new_email: newEmail?.trim() || null,
    }),
  });
}

// 현재 세션 사용자. 미인증이면 401 throw (AuthProvider 가 null 처리).
export async function getMe(): Promise<AuthUser> {
  return fetchJSON<AuthUser>(`/auth/me`);
}

// 첫 관리자 등록 필요 여부 (user 0명). 로그인 페이지가 이걸로 분기.
export async function bootstrapNeeded(): Promise<boolean> {
  const r = await fetchJSON<{ needed: boolean }>(`/auth/bootstrap-needed`);
  return r.needed;
}

// 첫 관리자 + 조직 생성 (user 0명일 때만). 성공 시 자동 로그인(쿠키).
export async function bootstrap(
  orgName: string,
  email: string,
  password: string,
  displayName?: string,
): Promise<AuthUser> {
  return fetchJSON<AuthUser>(`/auth/bootstrap`, {
    method: "POST",
    body: JSON.stringify({
      org_name: orgName,
      email,
      password,
      display_name: displayName?.trim() || null,
    }),
  });
}

// ── 조직 멤버 관리 (루트 관리자 전용) ──────────────────────────
// self-signup 없음 — 관리자가 멤버 계정을 발급(현재 조직 space 에 합류).

export interface Member {
  id: string;
  email: string;
  display_name: string | null;
  role: string;
}

export async function adminCreateUser(
  email: string,
  password: string,
  displayName?: string,
  role: "member" | "admin" = "member",
): Promise<Member> {
  return fetchJSON<Member>(`/auth/admin/users`, {
    method: "POST",
    body: JSON.stringify({
      email,
      password,
      display_name: displayName?.trim() || null,
      role,
    }),
  });
}

export async function adminListMembers(): Promise<Member[]> {
  return fetchJSON<Member[]>(`/auth/admin/members`);
}

export async function adminDeleteUser(
  userId: string,
): Promise<{ ok: boolean; deleted_user_id: string }> {
  return fetchJSON(`/auth/admin/users/${userId}`, { method: "DELETE" });
}

export async function logout(): Promise<void> {
  await fetchJSON<{ ok: boolean }>(`/auth/logout`, { method: "POST" });
}

export async function switchSpace(spaceId: string): Promise<AuthUser> {
  return fetchJSON<AuthUser>(`/auth/switch-space`, {
    method: "POST",
    body: JSON.stringify({ space_id: spaceId }),
  });
}

// ── ask 세션 서버 동기화 (단계 B) ──────────────────────────────
// 세션/메시지는 소유자 본인만(서버가 강제), 프로젝트는 조직 공유. write-through 미러.

export interface AskStoreExport {
  projects: unknown[];
  sessions: unknown[];
}

// 현재 계정의 전체 대화 store 를 서버에 덮어쓴다 (fire-and-forget 미러).
export async function syncAskStore(
  projects: unknown[],
  sessions: unknown[],
): Promise<{ ok: boolean }> {
  return fetchJSON(`/sessions/sync`, {
    method: "PUT",
    body: JSON.stringify({ projects, sessions }),
  });
}

// 로그인 시 본인 세션(+조직 공유 프로젝트)을 서버에서 받아 localStorage 복원.
export async function exportAskStore(): Promise<AskStoreExport> {
  return fetchJSON<AskStoreExport>(`/sessions/export`);
}

export { API_BASE };


// ============================================================================
// Wiki API (D10 llm_wiki, 2026-05-26)
// ============================================================================

export interface WikiPageListItem {
  id: string;
  topic_id: string | null;
  slug: string;
  title: string;
  description: string | null;
  body_status: "issues" | "pending" | "completed";  // 2026-05-27 rename: ready→issues
  body_generated_at: string | null;
  body_processing_started_at: string | null;        // NOT NULL ⇒ writer 진행 중
  source_count: number;
  is_pinned: boolean;
  keywords: string[];
  created_at: string;
  updated_at: string;
}

export interface WikiPageListResponse {
  total: number;
  pages: WikiPageListItem[];
}

export interface WikiSource {
  item_id: string;
  title: string | null;
  summary: string | null;
  source_type: string;
  source_url: string | null;
  confidence: number | null;
  role: string | null;
  user_action: string | null;
  tags: string[];
  user_notes: string | null;
  is_read: boolean;
  attachment_count: number;
}

export interface WikiCrossLink {
  slug: string;
  title: string;
  shared_items: number;
}

export interface WikiPageDetail {
  id: string;
  topic_id: string | null;
  slug: string;
  title: string;
  description: string | null;
  variant: string;
  body: string | null;
  body_status: "empty" | "generating" | "ready" | "stale";
  body_model: string | null;
  body_prompt_version: string | null;
  body_generated_at: string | null;
  latest_version: number;
  is_pinned: boolean;
  user_overrides: string | null;
  keywords: string[];
  sources: WikiSource[];
  cross_links: WikiCrossLink[];
  user_notes_combined: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface WikiKeywordsUpdateRequest {
  add?: string[];
  remove?: string[];
}

export interface WikiKeywordsUpdateResponse {
  slug: string;
  keywords: string[];
  added: string[];
  removed: string[];
}

export interface WikiKeywordSuggestion {
  keyword: string;
  usage_count: number;
}

export interface WikiKeywordSearchResponse {
  query: string;
  suggestions: WikiKeywordSuggestion[];
  total: number;     // 매칭 distinct 키워드 총 개수 (limit 무관 — '더 보기' 판단용)
}

export interface WikiSearchHit {
  page_id: string;
  slug: string;
  title: string;
  description: string | null;
  score: number;
  body_excerpt: string | null;
  source_count: number;
  body_status: string;
  matched_in: string;
}

export interface WikiSearchResponse {
  query: string;
  hits: WikiSearchHit[];
}

export interface WikiRegenerateResponse {
  page_id: string;
  slug: string;
  ok: boolean;
  body_length: number | null;
  version_number: number | null;
  duration_ms: number;
  error: string | null;
}

// sort: recent(최신) | oldest(오래된) | alpha(가나다) | alpha_desc(역순)
export type WikiSort = "recent" | "oldest" | "alpha" | "alpha_desc";

export async function listWikiPages(
  opts: {
    status?: string; q?: string; keyword?: string[];
    sort?: WikiSort; limit?: number; offset?: number;
  } = {},
): Promise<WikiPageListResponse> {
  const params = new URLSearchParams();
  if (opts.status) params.set("status", opts.status);
  if (opts.q) params.set("q", opts.q);
  // 다중 키워드 (AND) — ?keyword=A&keyword=B
  for (const k of opts.keyword ?? []) params.append("keyword", k);
  if (opts.sort) params.set("sort", opts.sort);
  params.set("limit", String(opts.limit ?? 50));
  params.set("offset", String(opts.offset ?? 0));
  return fetchJSON<WikiPageListResponse>(`/wiki?${params.toString()}`);
}

export async function getWikiPage(
  slug: string,
  opts: { regenerate?: boolean } = {},
): Promise<WikiPageDetail> {
  const params = new URLSearchParams();
  if (opts.regenerate) params.set("regenerate", "true");
  const qs = params.toString();
  return fetchJSON<WikiPageDetail>(`/wiki/${encodeURIComponent(slug)}${qs ? "?" + qs : ""}`);
}

export interface WikiByItem {
  slug: string;
  title: string;
  body_status: string; // 'issues' | 'pending' | 'completed'
}

// item 의 정체성 위키(self/primary) — ask URL-paste 후 위키 생성/합성 폴링용. 없으면 null.
export async function getWikiByItem(itemId: string): Promise<WikiByItem | null> {
  const r = await fetchJSON<{ wiki: WikiByItem | null }>(`/wiki/by-item/${itemId}`);
  return r.wiki;
}

// 여러 slug 의 현재 body_status 를 한 번에 — ask related_wikis 배지 live 갱신용.
export async function getWikiStatuses(slugs: string[]): Promise<Record<string, string>> {
  if (slugs.length === 0) return {};
  const r = await fetchJSON<{ statuses: Record<string, string> }>(`/wiki/statuses`, {
    method: "POST",
    body: JSON.stringify({ slugs }),
  });
  return r.statuses;
}

export async function regenerateWikiPage(slug: string): Promise<WikiRegenerateResponse> {
  return fetchJSON<WikiRegenerateResponse>(`/wiki/${encodeURIComponent(slug)}/regenerate`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

// PATCH /wiki/{slug} — title / description / body 수동 편집 (2026-05-27).
// 셋 다 optional. 적어도 하나 제공 필요. body 변경 시 backend 가 body_status='ready'
// + new wiki_page_versions row + Qdrant body embedding 재upsert.
export interface WikiPageEditRequest {
  title?: string;
  description?: string;
  body?: string;
}

export async function updateWikiPage(
  slug: string,
  payload: WikiPageEditRequest,
): Promise<WikiPageDetail> {
  return fetchJSON<WikiPageDetail>(`/wiki/${encodeURIComponent(slug)}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

// DELETE /wiki/{slug} — wiki + 연결 items (raw DB) 영구 삭제. irreversible.
// CASCADE 가 양방향이라 items 삭제 시 다른 wiki 의 sources 에서도 자동 제거.
export interface WikiPageDeleteResponse {
  deleted_wiki_slug: string;
  deleted_wiki_page_id: string;
  deleted_items_count: number;
  affected_other_wikis_count: number;
  qdrant_wiki_status: number;
  qdrant_items_status_sum: number;
}

export async function deleteWikiPage(slug: string): Promise<WikiPageDeleteResponse> {
  return fetchJSON<WikiPageDeleteResponse>(`/wiki/${encodeURIComponent(slug)}`, {
    method: "DELETE",
  });
}

export async function searchWikiPages(query: string, top_k = 10): Promise<WikiSearchResponse> {
  return fetchJSON<WikiSearchResponse>(`/wiki/search`, {
    method: "POST",
    body: JSON.stringify({ query, top_k }),
  });
}

export async function updateWikiKeywords(
  slug: string,
  payload: WikiKeywordsUpdateRequest,
): Promise<WikiKeywordsUpdateResponse> {
  return fetchJSON<WikiKeywordsUpdateResponse>(
    `/wiki/${encodeURIComponent(slug)}/keywords`,
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
  );
}

// ── Wiki stats + batch regenerate (2026-05-27) ─────────────────

// 2026-05-27 rename: ready→issues (처리 못 끝낸 잔여 자료)
export interface WikiStatsResponse {
  issues: number;      // 처리 실패 / stuck / 잔여 — 사용자 일괄 합성 트리거 대상
  pending: number;     // 처리 대기/진행
  completed: number;   // body 합성 완료
  total: number;
}

export async function getWikiStats(): Promise<WikiStatsResponse> {
  return fetchJSON<WikiStatsResponse>(`/wiki/_meta/stats`);
}

export interface WikiBatchRegenerateRequest {
  status: "issues" | "pending";  // issues (실패 잔여) or pending
  limit?: number;
}

export interface WikiBatchRegenerateResponse {
  status: string;
  dispatched: number;
  estimated_seconds: number;
}

// fire-and-forget — request 즉시 응답, 실제 합성은 backend BackgroundTask.
// 응답의 dispatched 만큼 page 가 'generating' 으로 마킹됨 → frontend 는 stats
// polling 으로 진행 확인 (몇 초 간격).
export async function batchRegenerateWiki(
  body: WikiBatchRegenerateRequest,
): Promise<WikiBatchRegenerateResponse> {
  return fetchJSON<WikiBatchRegenerateResponse>(`/wiki/_meta/batch_regenerate`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function searchWikiKeywords(q: string, limit = 20): Promise<WikiKeywordSearchResponse> {
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  params.set("limit", String(limit));
  return fetchJSON<WikiKeywordSearchResponse>(`/wiki/_keywords/search?${params.toString()}`);
}
