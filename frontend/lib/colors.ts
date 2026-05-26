// 그래프 노드 + 사이드바 표시 색상 — 한 곳에 모아 GraphView/TopicsTree/Search 등이 공유.
// 색상 의미가 일관돼야 사용자가 graph 와 list 사이를 시각적으로 매칭 가능.
//
// 그룹 (Phase 2.5 wave-3, 사용자 요구 — 같은 의미는 같은 색 계열):
//   📄 Articles (녹색): pdf, arxiv, doi, document, paperswithcode — 모두 "글 자료"
//   🎥 Video    (빨강): youtube, youtube_playlist, yt, ytpl       — 동영상
//   💻 Code    (보라): github                                      — 소스 코드
//   🌐 Web     (파랑): url                                          — 일반 웹
//   💬 Note    (시안): telegram, slack, manual                     — 짧은 메모
// 그룹 안에서 미세 명도 차로 modality 구분 (PDF=진녹, arxiv=중녹, document=연녹).

// item 의 source_type 별 색상
export const SOURCE_TYPE_COLORS: Record<string, string> = {
  // 📄 Articles — 녹색 계열
  pdf: "#059669",              // 진녹 (논문 PDF 본문)
  arxiv: "#10b981",            // 녹  (arxiv abstract)
  document: "#34d399",         // 연녹 (DOCX/PPTX/TXT/MD)

  // 🎥 Video — 빨강 계열
  youtube: "#dc2626",          // 빨강 (단일 영상)
  youtube_playlist: "#b91c1c", // 진빨강 (playlist)

  // 💻 Code — 보라
  github: "#8b5cf6",

  // 🌐 Web — 파랑
  url: "#3b82f6",

  // 💬 Note — 시안 계열
  telegram: "#06b6d4",
  slack: "#0891b2",            // 진시안 (Slack 메시지)
  manual: "#67e8f9",           // 연시안 (수동 입력)
};

export const DEFAULT_NODE_COLOR = "#71717a";

export function sourceTypeColor(source: string | undefined | null): string {
  if (!source) return DEFAULT_NODE_COLOR;
  return SOURCE_TYPE_COLORS[source] || DEFAULT_NODE_COLOR;
}

// topic 노드 색상 — primary_external_id.kind 별 (같은 그룹 색 일관)
export const TOPIC_KIND_COLORS: Record<string, string> = {
  // 📄 Articles
  arxiv: "#10b981",
  doi: "#34d399",               // 연녹 (DOI publication)
  paperswithcode: "#059669",    // 진녹 (paper + code 묶음)
  // 🎥 Video
  yt: "#dc2626",
  ytpl: "#b91c1c",
  // 💻 Code
  github: "#8b5cf6",
};

export const DEFAULT_TOPIC_COLOR = "#f97316";   // orange — LinkMind brand (외부 ID 없는 fallback)

export function topicKindColor(
  primaryExternalId: Record<string, string> | null | undefined,
): string {
  const kind = primaryExternalId?.kind;
  if (!kind) return DEFAULT_TOPIC_COLOR;
  return TOPIC_KIND_COLORS[kind] || DEFAULT_TOPIC_COLOR;
}

// ─── 그룹 정의 (Legend 가 사용) ────────────────────────────────────
// "이 색은 어떤 의미 그룹인지" 한눈에 보이도록.
export interface ColorGroup {
  key: string;
  label: { ko: string; en: string };
  groupColor: string;     // 그룹 대표 색 (Legend 헤더)
  sourceTypes: string[];  // 이 그룹에 속한 source_type
  topicKinds: string[];   // 이 그룹에 속한 topic kind
}

export const COLOR_GROUPS: ColorGroup[] = [
  {
    key: "articles",
    label: { ko: "📄 논문 · 문서", en: "📄 Articles" },
    groupColor: "#10b981",
    sourceTypes: ["pdf", "arxiv", "document"],
    topicKinds: ["arxiv", "doi", "paperswithcode"],
  },
  {
    key: "video",
    label: { ko: "🎥 동영상", en: "🎥 Video" },
    groupColor: "#dc2626",
    sourceTypes: ["youtube", "youtube_playlist"],
    topicKinds: ["yt", "ytpl"],
  },
  {
    key: "code",
    label: { ko: "💻 코드", en: "💻 Code" },
    groupColor: "#8b5cf6",
    sourceTypes: ["github"],
    topicKinds: ["github"],
  },
  {
    key: "web",
    label: { ko: "🌐 웹 페이지", en: "🌐 Web" },
    groupColor: "#3b82f6",
    sourceTypes: ["url"],
    topicKinds: [],
  },
  {
    key: "note",
    label: { ko: "💬 메모", en: "💬 Note" },
    groupColor: "#06b6d4",
    sourceTypes: ["telegram", "slack", "manual"],
    topicKinds: [],
  },
];

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
