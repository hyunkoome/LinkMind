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
  SearchRequest,
  SearchResponse,
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

export interface ItemListCard {
  id: string;
  source_type: string;
  source_url: string | null;
  title: string | null;
  summary_preview: string | null;
  raw_preview: string | null;
  raw_length: number;
  domain: string | null;
  fetch_error_kind: string | null;
  fetch_error_message: string | null;
  has_user_notes: boolean;
  user_notes_preview: string | null;
  tags: string[];
  is_read: boolean;
  ingested_at: string;
  attachments: ItemAttachmentSummary[];
}

export interface ItemListFacets {
  kind: Record<string, number>;
  source_type: Record<string, number>;
  domain: Record<string, number>;
}

export interface ItemListResponse {
  items: ItemListCard[];
  total: number;
  page: number;
  page_size: number;
  facets: ItemListFacets;
}

export interface ItemListFilters {
  kind?: string;
  source_type?: string;
  domain?: string;
  has_user_notes?: boolean;
  has_summary?: boolean;
  q?: string;
  sort?: string;
  page?: number;
  page_size?: number;
}

export async function listItems(filters: ItemListFilters): Promise<ItemListResponse> {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(filters)) {
    if (v === undefined || v === null || v === "") continue;
    params.set(k, String(v));
  }
  const qs = params.toString();
  return fetchJSON<ItemListResponse>(`/items${qs ? "?" + qs : ""}`);
}

export async function appendItemNote(
  itemId: string, note: string,
): Promise<ItemDetail> {
  return fetchJSON<ItemDetail>(`/items/${itemId}/notes`, {
    method: "POST",
    body: JSON.stringify({ note }),
  });
}

export interface DeleteItemResponse {
  deleted: boolean;
  item_id: string;
  qdrant_status: number;
}

export async function deleteItem(itemId: string): Promise<DeleteItemResponse> {
  return fetchJSON<DeleteItemResponse>(`/items/${itemId}`, {
    method: "DELETE",
  });
}

export interface LinkCategoryResponse {
  linked: boolean;
  category_slug: string;
  topic_id: string;
  topic_slug: string;
}

export async function linkItemCategory(
  itemId: string, slug: string,
): Promise<LinkCategoryResponse> {
  return fetchJSON<LinkCategoryResponse>(
    `/items/${itemId}/categories/${encodeURIComponent(slug)}`,
    { method: "POST" },
  );
}

export interface CategorySummary {
  id: string;
  slug: string;
  label: string;
  description: string | null;
  synonyms: string[];
  color: string | null;
  pinned: boolean;
  topic_count: number;
  item_count: number;
}

export async function listCategories(limit = 1000): Promise<CategorySummary[]> {
  return fetchJSON<CategorySummary[]>(`/categories?limit=${limit}`);
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

// ── Search (Qdrant 의미 검색 — graph 의 FTS 와 별개) ────────────

export async function searchSemantic(body: SearchRequest): Promise<SearchResponse> {
  return fetchJSON<SearchResponse>(`/search`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export { API_BASE };
