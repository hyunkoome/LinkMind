// backend/schemas/models.py 의 GraphNode/GraphEdge/GraphResponse 와 1:1 대응.
// 변경 시 두 곳 모두 동기화.

export interface GraphNodeData {
  id: string;                    // "topic:<uuid>" | "item:<uuid>"
  label: string;
  type: "topic" | "item";
  // topic 전용
  slug?: string;
  title?: string | null;
  item_count?: number;
  primary_external_id?: Record<string, string>;
  // item 전용
  source_type?: string;          // pdf / url / youtube / github / document / telegram / ...
  source_url?: string | null;
  summary?: string | null;
  tags?: string[];
  is_read?: boolean;
  has_notes?: boolean;
  ingested_at?: string | null;
}

export interface GraphNode {
  data: GraphNodeData;
}

export interface GraphEdgeData {
  id: string;                    // "edge:<item-uuid>:<topic-uuid>"
  source: string;                // "item:<uuid>"
  target: string;                // "topic:<uuid>"
  role: string;                  // paper | code | video | playlist | blog | note | item
  confidence?: number;
  link_source?: "auto" | "manual" | string;
}

export interface GraphEdge {
  data: GraphEdgeData;
}

export interface GraphResponse {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

// GET /items/{id} — ItemDetail
export interface ItemAttachment {
  id: string;
  role: string | null;
  mime_type: string | null;
  file_size: number | null;
  file_hash: string;
  caption: string | null;
  width: number | null;
  height: number | null;
}

export interface ItemDetail {
  id: string;
  source_type: string;
  source_id: string | null;
  source_url: string | null;
  source_metadata: Record<string, unknown>;
  title: string | null;
  summary: string | null;
  raw_content: string;
  categories: string[];
  tags: string[];
  language: string | null;
  source_created_at: string | null;
  ingested_at: string;
  updated_at: string;
  user_notes: string | null;
  user_notes_updated_at: string | null;
  is_read: boolean;
  read_at: string | null;
  attachments: ItemAttachment[];
}

export interface ItemUpdateRequest {
  user_notes?: string | null;
  is_read?: boolean | null;
}
