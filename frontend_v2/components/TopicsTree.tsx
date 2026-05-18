"use client";

import type { GraphResponse, GraphNodeData } from "@/types/graph";

interface TopicsTreeProps {
  data: GraphResponse;
  selectedTopicId: string | null;
  onTopicClick: (topicNodeId: string) => void;
  onSearchSubmit: (query: string) => void;
  searchQuery: string;
  onSearchChange: (q: string) => void;
}

export default function TopicsTree({
  data,
  selectedTopicId,
  onTopicClick,
  onSearchSubmit,
  searchQuery,
  onSearchChange,
}: TopicsTreeProps) {
  const topics: GraphNodeData[] = data.nodes
    .filter((n) => n.data.type === "topic")
    .map((n) => n.data)
    .sort((a, b) => (b.item_count || 0) - (a.item_count || 0));

  return (
    <aside className="w-64 shrink-0 h-full overflow-hidden flex flex-col border-r border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900">
      <header className="p-3 border-b border-zinc-200 dark:border-zinc-800">
        <h1 className="text-base font-semibold mb-2 text-orange-600 dark:text-orange-400">
          LinkMind
        </h1>
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
            placeholder="검색 (Enter)…"
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
            ✕ 검색 초기화 (전체 그래프)
          </button>
        )}
      </header>

      <div className="flex-1 overflow-y-auto p-2">
        <div className="text-[10px] uppercase tracking-wider text-zinc-500 mb-1 px-1">
          Topics ({topics.length})
        </div>
        {topics.length === 0 && (
          <div className="text-xs text-zinc-500 dark:text-zinc-400 px-2 py-3">
            topic 없음 — ingest 후 자동 생성됨
          </div>
        )}
        <ul className="space-y-0.5">
          {topics.map((t) => {
            const isSelected = selectedTopicId === t.id;
            return (
              <li key={t.id}>
                <button
                  type="button"
                  onClick={() => onTopicClick(t.id)}
                  className={`w-full text-left px-2 py-1.5 rounded text-xs transition ${
                    isSelected
                      ? "bg-orange-100 dark:bg-orange-900/30 text-orange-700 dark:text-orange-300 font-medium"
                      : "hover:bg-zinc-100 dark:hover:bg-zinc-800 text-zinc-700 dark:text-zinc-300"
                  }`}
                  title={t.slug || ""}
                >
                  <span className="block truncate">{t.label}</span>
                  <span className="block text-[10px] text-zinc-500">
                    {t.item_count ?? 0} items · {t.slug}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </div>

      <footer className="p-2 text-[10px] text-zinc-400 border-t border-zinc-200 dark:border-zinc-800">
        Phase 2.5 graph UI · cytoscape
      </footer>
    </aside>
  );
}
