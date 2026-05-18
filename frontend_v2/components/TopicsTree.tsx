"use client";

import { topicKindColor } from "@/lib/colors";
import { useT } from "@/lib/i18n/context";
import type { GraphResponse, GraphNodeData } from "@/types/graph";

interface TopicsTreeProps {
  data: GraphResponse;
  selectedTopicId: string | null;
  onTopicClick: (topicNodeId: string) => void;
  onSearchSubmit: (query: string) => void;
  searchQuery: string;
  onSearchChange: (q: string) => void;
  isSubsetView: boolean;
  onReturnToAll: () => void;
}

export default function TopicsTree({
  data,
  selectedTopicId,
  onTopicClick,
  onSearchSubmit,
  searchQuery,
  onSearchChange,
  isSubsetView,
  onReturnToAll,
}: TopicsTreeProps) {
  const { t } = useT();
  const topics: GraphNodeData[] = data.nodes
    .filter((n) => n.data.type === "topic")
    .map((n) => n.data)
    .sort((a, b) => (b.item_count || 0) - (a.item_count || 0));

  return (
    <aside className="w-64 shrink-0 h-full overflow-hidden flex flex-col border-r border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900">
      <header className="p-3 border-b border-zinc-200 dark:border-zinc-800">
        <h1 className="text-base font-semibold mb-2 text-orange-600 dark:text-orange-400">
          {t.app.title}
        </h1>

        {/* 부분 view 일 때만 "전체 복귀" 버튼 — 검색 상자 위에 배치 (눈에 띔) */}
        {isSubsetView && (
          <button
            type="button"
            onClick={onReturnToAll}
            className="w-full mb-2 px-2 py-2 text-xs bg-orange-500 hover:bg-orange-600 text-white rounded font-medium shadow animate-pulse"
            title={t.graph.subsetView}
          >
            {t.graph.backToAll}
          </button>
        )}

        <form
          onSubmit={(e) => {
            e.preventDefault();
            onSearchSubmit(searchQuery);
          }}
        >
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => onSearchChange(e.target.value)}
            placeholder={t.topicsTree.searchPlaceholder}
            className="w-full px-2 py-1.5 text-sm bg-zinc-100 dark:bg-zinc-800 border border-zinc-300 dark:border-zinc-700 rounded focus:outline-none focus:ring-1 focus:ring-orange-500"
          />
        </form>
        {searchQuery && (
          <button
            type="button"
            onClick={() => {
              onSearchChange("");
              onSearchSubmit("");
            }}
            className="mt-1 text-[10px] text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300"
          >
            {t.topicsTree.clearSearch}
          </button>
        )}
      </header>

      <div className="flex-1 overflow-y-auto p-2">
        <div className="text-[10px] uppercase tracking-wider text-zinc-500 mb-1 px-1">
          {t.topicsTree.header} ({topics.length})
        </div>
        {topics.length === 0 && (
          <div className="text-xs text-zinc-500 dark:text-zinc-400 px-2 py-3">
            {t.topicsTree.empty}
          </div>
        )}
        <ul className="space-y-0.5">
          {topics.map((topic) => {
            const isSelected = selectedTopicId === topic.id;
            // graph 노드와 같은 색 — primary_external_id.kind 별 (arxiv/github/yt 등)
            const dotColor = topicKindColor(topic.primary_external_id);
            return (
              <li key={topic.id}>
                <button
                  type="button"
                  onClick={() => onTopicClick(topic.id)}
                  className={`w-full text-left px-2 py-1.5 rounded text-xs transition flex items-start gap-2 ${
                    isSelected
                      ? "bg-orange-100 dark:bg-orange-900/30 text-orange-700 dark:text-orange-300 font-medium ring-2 ring-orange-400"
                      : "hover:bg-zinc-100 dark:hover:bg-zinc-800 text-zinc-700 dark:text-zinc-300"
                  }`}
                  title={topic.slug || ""}
                >
                  {/* graph 노드 색상과 동일 — 시각적 매칭 */}
                  <span
                    className="mt-0.5 inline-block w-2.5 h-2.5 rounded-full shrink-0"
                    style={{ backgroundColor: dotColor }}
                    aria-label="topic color"
                  />
                  <span className="flex-1 min-w-0">
                    <span className="block truncate">{topic.label}</span>
                    <span className="block text-[10px] text-zinc-500">
                      {topic.item_count ?? 0} {t.topicsTree.itemsCount} · {topic.slug}
                    </span>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </div>

      <footer className="p-2 text-[10px] text-zinc-400 border-t border-zinc-200 dark:border-zinc-800">
        Phase 2.5 · 3D graph (three.js + force-graph)
      </footer>
    </aside>
  );
}
