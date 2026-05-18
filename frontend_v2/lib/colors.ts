// 그래프 노드 + 사이드바 표시 색상 — 한 곳에 모아 GraphView/TopicsTree/Search 등이 공유.
// 색상 의미가 일관돼야 사용자가 graph 와 list 사이를 시각적으로 매칭 가능.

// item 의 source_type 별 색상 (Tailwind palette 의 500 톤, dark 배경 친화)
export const SOURCE_TYPE_COLORS: Record<string, string> = {
  pdf: "#ef4444",              // red — 논문/문서
  url: "#3b82f6",              // blue — 일반 웹
  arxiv: "#10b981",            // green — arxiv abstract
  github: "#8b5cf6",           // purple — GitHub repo
  youtube: "#dc2626",          // dark red — YouTube 단일
  youtube_playlist: "#b91c1c", // 더 진한 red — playlist
  document: "#f59e0b",         // amber — DOCX/PPTX/TXT/MD
  telegram: "#06b6d4",         // cyan — 텔레그램 note
  slack: "#a855f7",            // violet — Slack
  manual: "#71717a",           // zinc — 수동 입력
};

export const DEFAULT_NODE_COLOR = "#71717a";

export function sourceTypeColor(source: string | undefined | null): string {
  if (!source) return DEFAULT_NODE_COLOR;
  return SOURCE_TYPE_COLORS[source] || DEFAULT_NODE_COLOR;
}

// topic 노드 색상 — primary_external_id.kind 별. graph 와 TopicsTree 둘 다 같은 색.
// kind 가 없으면 default orange (LinkMind 의 brand 색).
export const TOPIC_KIND_COLORS: Record<string, string> = {
  arxiv: "#10b981",            // green (arxiv item 색상과 일관)
  doi: "#06b6d4",              // cyan (DOI publication)
  github: "#8b5cf6",            // purple (GitHub topic — repo cluster)
  yt: "#dc2626",                // red (YouTube video cluster)
  ytpl: "#b91c1c",              // dark red (playlist cluster)
  paperswithcode: "#10b981",    // green (paper 와 동일)
};

export const DEFAULT_TOPIC_COLOR = "#f97316";   // orange — LinkMind brand

export function topicKindColor(
  primaryExternalId: Record<string, string> | null | undefined,
): string {
  const kind = primaryExternalId?.kind;
  if (!kind) return DEFAULT_TOPIC_COLOR;
  return TOPIC_KIND_COLORS[kind] || DEFAULT_TOPIC_COLOR;
}

// 사람이 읽기 좋은 label — source_type / topic kind 의 한국어/영어 설명
export const SOURCE_TYPE_LABEL: Record<string, { ko: string; en: string }> = {
  pdf: { ko: "PDF / 논문", en: "PDF / paper" },
  url: { ko: "웹 페이지", en: "web page" },
  arxiv: { ko: "arxiv abstract", en: "arxiv abstract" },
  github: { ko: "GitHub repo", en: "GitHub repo" },
  youtube: { ko: "YouTube 영상", en: "YouTube video" },
  youtube_playlist: { ko: "YouTube playlist", en: "YouTube playlist" },
  document: { ko: "Office 문서 (DOCX/PPTX/TXT/MD)", en: "Office doc (DOCX/PPTX/TXT/MD)" },
  telegram: { ko: "Telegram 메모", en: "Telegram note" },
  slack: { ko: "Slack", en: "Slack" },
  manual: { ko: "수동 입력", en: "manual" },
};

export const TOPIC_KIND_LABEL: Record<string, { ko: string; en: string }> = {
  arxiv: { ko: "arxiv 논문 cluster", en: "arxiv paper cluster" },
  doi: { ko: "DOI cluster", en: "DOI cluster" },
  github: { ko: "GitHub repo cluster", en: "GitHub repo cluster" },
  yt: { ko: "YouTube video cluster", en: "YouTube video cluster" },
  ytpl: { ko: "YouTube playlist cluster", en: "YouTube playlist cluster" },
  paperswithcode: { ko: "paperswithcode cluster", en: "paperswithcode cluster" },
};
