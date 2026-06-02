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
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
  });
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
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  return res.json();
}

// ── Ask (대화형 RAG, 2026-05-27) ───────────────────────────────

export interface AskRequest {
  question: string;
  top_k?: number;
  llm_provider?: string;
  llm_model?: string;
  // ask 에서 URL 붙여 방금 ingest 한 item — context 최상단 강제 포함 (URL-paste-ingest)
  pin_item_ids?: string[];
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
