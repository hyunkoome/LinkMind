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

export async function getGraphTopics(limit = 5000): Promise<GraphResponse> {
  return fetchJSON<GraphResponse>(`/graph/topics?limit=${limit}`);
}

// 카테고리 (키워드) 노드 + 그 안의 topic 들. 시작 화면 — 가벼움.
export async function getGraphCategories(limit = 500): Promise<GraphResponse> {
  return fetchJSON<GraphResponse>(`/graph/categories?limit=${limit}`);
}

// 카테고리 클릭 시 expand — 그 카테고리의 topic + item 전부.
export async function expandGraphCategory(slug: string): Promise<GraphResponse> {
  return fetchJSON<GraphResponse>(`/graph/category/${encodeURIComponent(slug)}`);
}

// 토픽 클릭 시 expand — 그 토픽 1개 + 그 안의 모든 item.
export async function expandGraphTopic(topicUuid: string): Promise<GraphResponse> {
  return fetchJSON<GraphResponse>(`/graph/topic/${encodeURIComponent(topicUuid)}`);
}

// detail panel — topic 정보 + 그 안의 모든 item.
export interface TopicDetailItem {
  id: string;
  source_type: string;
  source_url: string | null;
  title: string | null;
  summary: string | null;
  tags: string[];
  role: string;
  confidence: number;
  source: string;
  note: string | null;
}
export interface TopicDetailResponse {
  id: string;
  slug: string;
  title: string;
  description: string | null;
  primary_external_id: { kind: string; value: string } | null;
  tags: string[];
  items: TopicDetailItem[];
}
export async function getTopic(idOrSlug: string): Promise<TopicDetailResponse> {
  return fetchJSON<TopicDetailResponse>(`/topics/${encodeURIComponent(idOrSlug)}`);
}

// detail panel — category 정보 + 그 안의 topics.
export interface CategoryDetailTopic {
  id: string;
  slug: string;
  title: string;
  primary_external_id: { kind: string; value: string } | null;
  tags: string[];
  item_count: number;
}
export interface CategoryDetailResponse {
  id: string;
  slug: string;
  label: string;
  description: string | null;
  synonyms: string[];
  color: string | null;
  pinned: boolean;
  topics: CategoryDetailTopic[];
}
export async function getCategory(slug: string): Promise<CategoryDetailResponse> {
  return fetchJSON<CategoryDetailResponse>(`/categories/${encodeURIComponent(slug)}`);
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
  body_status: "empty" | "generating" | "ready" | "stale";
  body_generated_at: string | null;
  source_count: number;
  is_pinned: boolean;
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

export async function listWikiPages(
  opts: { status?: string; q?: string; keyword?: string; limit?: number; offset?: number } = {},
): Promise<WikiPageListResponse> {
  const params = new URLSearchParams();
  if (opts.status) params.set("status", opts.status);
  if (opts.q) params.set("q", opts.q);
  if (opts.keyword) params.set("keyword", opts.keyword);
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

// 2026-05-27 통일: 3 status (backend body_status 와 동일)
export interface WikiStatsResponse {
  ready: number;       // body 없음, lazy 처리 대기
  pending: number;     // 처리 대기/진행 중 (옛 stale + generating 통합)
  completed: number;   // body 있음 (옛 'ready')
  total: number;
}

export async function getWikiStats(): Promise<WikiStatsResponse> {
  return fetchJSON<WikiStatsResponse>(`/wiki/_meta/stats`);
}

export interface WikiBatchRegenerateRequest {
  status: "ready" | "pending";   // ready (옛 empty) or pending (옛 stale)
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
