"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { sourceTypeColor, topicKindColor } from "@/lib/colors";
import { useT } from "@/lib/i18n/context";
import type { GraphResponse, GraphNodeData } from "@/types/graph";

interface TopicsTreeProps {
  data: GraphResponse;
  /** selectedNodeFullId: "topic:<uuid>" | "category:<uuid>" | "item:<uuid>" | null */
  selectedNodeFullId: string | null;
  /** selected + 그와 같은 묶음의 모든 노드 fullId. sidebar 부드러운 highlight 에 사용. */
  relatedIds?: Set<string>;
  /** 클릭한 노드의 fullId (e.g. "category:<uuid>" / "topic:<uuid>") — page.tsx 가 분기 */
  onNodeSelect: (fullId: string) => void;
  onSearchSubmit: (query: string) => void;
  searchQuery: string;
  onSearchChange: (q: string) => void;
  isSubsetView: boolean;
  onReturnToAll: () => void;
  onReturnToPrevious?: () => void;
  /** Navigation history 의 깊이 — 0 이면 "← 이전" 비활성 */
  historyDepth?: number;
}

const LS_EXPANDED = "linkmind:tree-expanded-cats";

export default function TopicsTree({
  data,
  selectedNodeFullId,
  relatedIds,
  onNodeSelect,
  onSearchSubmit,
  searchQuery,
  onSearchChange,
  isSubsetView,
  onReturnToAll,
  onReturnToPrevious,
  historyDepth = 0,
}: TopicsTreeProps) {
  const { t, locale } = useT();
  const containerRef = useRef<HTMLDivElement>(null);

  const {
    categories,
    topicsByCategoryId,
    itemsByTopicId,
    orphanTopics,
    allTopics,
  } = useMemo(() => {
    const cats: GraphNodeData[] = [];
    const topicsMap = new Map<string, GraphNodeData>();
    const itemsMap = new Map<string, GraphNodeData>();
    const orphan: GraphNodeData[] = [];
    for (const n of data.nodes) {
      if (n.data.type === "category") cats.push(n.data);
      else if (n.data.type === "topic") topicsMap.set(n.data.id, n.data);
      else if (n.data.type === "item") itemsMap.set(n.data.id, n.data);
    }
    // category→topic 엣지 + item→topic 엣지 분리.
    const topicsBy: Record<string, GraphNodeData[]> = {};
    const itemsByTopic: Record<string, GraphNodeData[]> = {};
    const assignedTopicIds = new Set<string>();
    for (const e of data.edges) {
      if (e.data.source.startsWith("category:")) {
        const cid = e.data.source;
        const tidPrefixed = e.data.target;
        const topic = topicsMap.get(tidPrefixed);
        if (!topic) continue;
        (topicsBy[cid] ||= []).push(topic);
        assignedTopicIds.add(tidPrefixed);
      } else if (
        e.data.source.startsWith("item:") &&
        e.data.target.startsWith("topic:")
      ) {
        const item = itemsMap.get(e.data.source);
        if (!item) continue;
        (itemsByTopic[e.data.target] ||= []).push(item);
      }
    }
    for (const t of topicsMap.values()) {
      if (!assignedTopicIds.has(t.id)) orphan.push(t);
    }
    cats.sort((a, b) => (b.topic_count || 0) - (a.topic_count || 0));
    Object.values(topicsBy).forEach((list) =>
      list.sort((a, b) => (b.item_count || 0) - (a.item_count || 0)),
    );
    orphan.sort((a, b) => (b.item_count || 0) - (a.item_count || 0));
    return {
      categories: cats,
      topicsByCategoryId: topicsBy,
      itemsByTopicId: itemsByTopic,
      orphanTopics: orphan,
      allTopics: Array.from(topicsMap.values()).sort(
        (a, b) => (b.item_count || 0) - (a.item_count || 0),
      ),
    };
  }, [data]);

  // 펼침 상태 — 카테고리 별 (uuid set). localStorage 보존.
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      const raw = window.localStorage.getItem(LS_EXPANDED);
      if (raw) setExpanded(new Set(JSON.parse(raw)));
    } catch {
      /* ignore */
    }
  }, []);
  const toggleExpanded = (cid: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(cid)) next.delete(cid);
      else next.add(cid);
      try {
        window.localStorage.setItem(LS_EXPANDED, JSON.stringify([...next]));
      } catch {
        /* ignore */
      }
      return next;
    });
  };

  // selectedNodeFullId 가 토픽/아이템이고 그 토픽이 속한 카테고리가 있으면 자동 expand + scroll.
  // relatedIds 안에 topic 이 들어있으면 (item 선택 시 그 item 의 topic 들 포함) 그 카테고리도 펼침.
  useEffect(() => {
    if (!selectedNodeFullId) return;
    // 선택 + related topic 들 다 후보 — 어느 카테고리든 자식이면 그 카테고리 펼침
    const candidateTopicIds = new Set<string>();
    if (selectedNodeFullId.startsWith("topic:")) candidateTopicIds.add(selectedNodeFullId);
    if (relatedIds) {
      for (const id of relatedIds) {
        if (id.startsWith("topic:")) candidateTopicIds.add(id);
      }
    }
    if (candidateTopicIds.size > 0) {
      const toOpen: string[] = [];
      for (const [cid, list] of Object.entries(topicsByCategoryId)) {
        if (list.some((tp) => candidateTopicIds.has(tp.id))) {
          toOpen.push(cid);
        }
      }
      if (toOpen.length > 0) {
        setExpanded((prev) => {
          const next = new Set(prev);
          let changed = false;
          for (const cid of toOpen) {
            if (!next.has(cid)) {
              next.add(cid);
              changed = true;
            }
          }
          return changed ? next : prev;
        });
      }
    }
    // 선택된 항목으로 자동 scroll
    requestAnimationFrame(() => {
      const el = containerRef.current?.querySelector<HTMLElement>(
        `[data-tree-id="${selectedNodeFullId}"]`,
      );
      el?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
  }, [selectedNodeFullId, topicsByCategoryId, relatedIds]);

  // categories view 인지 판별 — 카테고리 노드가 1개 이상이면 트리 모드
  const showCategoryTree = categories.length > 0;

  return (
    <aside className="w-64 shrink-0 h-full overflow-hidden flex flex-col border-r border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900">
      <header className="p-3 border-b border-zinc-200 dark:border-zinc-800">
        <h1 className="text-base font-semibold mb-2 text-orange-600 dark:text-orange-400">
          {t.app.title}
        </h1>

        {/* navigation: 이전 + 전체 — isSubsetView 일 때만 */}
        {isSubsetView && (
          <div className="flex gap-1 mb-2">
            {onReturnToPrevious && historyDepth > 0 && (
              <button
                type="button"
                onClick={onReturnToPrevious}
                className="flex-1 px-2 py-2 text-xs bg-zinc-200 dark:bg-zinc-700 hover:bg-zinc-300 dark:hover:bg-zinc-600 text-zinc-800 dark:text-zinc-100 rounded font-medium"
                title={locale === "ko" ? "이전 그래프로 복귀" : "previous view"}
              >
                {locale === "ko" ? "← 이전" : "← back"}
              </button>
            )}
            <button
              type="button"
              onClick={onReturnToAll}
              className="flex-1 px-2 py-2 text-xs bg-orange-500 hover:bg-orange-600 text-white rounded font-medium shadow"
              title={t.graph.subsetView}
            >
              {locale === "ko" ? "← 전체" : t.graph.backToAll}
            </button>
          </div>
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

      <div ref={containerRef} className="flex-1 overflow-y-auto p-2">
        {showCategoryTree && (
          <>
            <div className="text-[10px] uppercase tracking-wider text-zinc-500 mb-1 px-1">
              {locale === "ko" ? "카테고리" : "Categories"} ({categories.length})
            </div>
            <ul className="space-y-0.5 mb-3">
              {categories.map((cat) => {
                // cat.id 는 fullId ("category:<uuid>") — TopicsTree 안에서는 그것 통일.
                const isOpen = expanded.has(cat.id);
                const isSelected = selectedNodeFullId === cat.id;
                const isRelated = !isSelected && (relatedIds?.has(cat.id) ?? false);
                const dotColor = cat.color || "#facc15";
                const childTopics = topicsByCategoryId[cat.id] || [];
                return (
                  <li key={cat.id} data-tree-id={cat.id}>
                    <div
                      className={`flex items-center rounded ${
                        isSelected
                          ? "bg-orange-100 dark:bg-orange-900/30 ring-2 ring-orange-400"
                          : isRelated
                            ? "bg-amber-50 dark:bg-amber-900/20"
                            : "hover:bg-zinc-100 dark:hover:bg-zinc-800"
                      }`}
                    >
                      <button
                        type="button"
                        onClick={() => toggleExpanded(cat.id)}
                        className="px-1 py-1.5 text-xs text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100"
                        aria-label={isOpen ? "collapse" : "expand"}
                      >
                        {isOpen ? "▾" : "▸"}
                      </button>
                      <button
                        type="button"
                        onClick={() => onNodeSelect(cat.id)}
                        className={`flex-1 text-left px-1 py-1.5 text-xs transition flex items-center gap-2 ${
                          isSelected
                            ? "text-orange-700 dark:text-orange-300 font-medium"
                            : "text-zinc-700 dark:text-zinc-300"
                        }`}
                        title={cat.slug || ""}
                      >
                        <span
                          className="inline-block w-2.5 h-2.5 rounded-full shrink-0"
                          style={{ backgroundColor: dotColor }}
                        />
                        <span className="flex-1 min-w-0">
                          <span className="block truncate">
                            {cat.pinned ? "📌 " : ""}
                            {cat.label}
                          </span>
                          <span className="block text-[10px] text-zinc-500">
                            {cat.topic_count ?? 0} topics · {cat.item_count ?? 0} items
                          </span>
                        </span>
                      </button>
                    </div>
                    {isOpen && childTopics.length > 0 && (
                      <ul className="ml-6 mt-0.5 mb-1 space-y-0.5 border-l border-zinc-200 dark:border-zinc-800 pl-2">
                        {childTopics.map((topic) => (
                          <TopicNode
                            key={topic.id}
                            topic={topic}
                            items={itemsByTopicId[topic.id] || []}
                            selectedNodeFullId={selectedNodeFullId}
                            relatedIds={relatedIds}
                            isOpen={expanded.has(topic.id)}
                            onToggle={() => toggleExpanded(topic.id)}
                            onNodeSelect={onNodeSelect}
                            itemsLabel={t.topicsTree.itemsCount}
                          />
                        ))}
                      </ul>
                    )}
                  </li>
                );
              })}
            </ul>
          </>
        )}

        {/* category 가 없는 topic (또는 'topics' view) */}
        {(showCategoryTree ? orphanTopics : allTopics).length > 0 && (
          <>
            <div className="text-[10px] uppercase tracking-wider text-zinc-500 mb-1 px-1">
              {t.topicsTree.header} (
              {(showCategoryTree ? orphanTopics : allTopics).length})
              {showCategoryTree && (
                <span className="ml-1 text-zinc-400">
                  {locale === "ko" ? " · 미분류" : " · uncategorized"}
                </span>
              )}
            </div>
            <ul className="space-y-0.5">
              {(showCategoryTree ? orphanTopics : allTopics).map((topic) => (
                <TopicNode
                  key={topic.id}
                  topic={topic}
                  items={itemsByTopicId[topic.id] || []}
                  selectedNodeFullId={selectedNodeFullId}
                  relatedIds={relatedIds}
                  isOpen={expanded.has(topic.id)}
                  onToggle={() => toggleExpanded(topic.id)}
                  onNodeSelect={onNodeSelect}
                  itemsLabel={t.topicsTree.itemsCount}
                  showSlugInline={true}
                />
              ))}
            </ul>
          </>
        )}
        {!showCategoryTree && allTopics.length === 0 && (
          <div className="text-xs text-zinc-500 dark:text-zinc-400 px-2 py-3">
            {t.topicsTree.empty}
          </div>
        )}
      </div>

      <footer className="p-2 text-[10px] text-zinc-400 border-t border-zinc-200 dark:border-zinc-800">
        Phase 2.5 · 3D graph (three.js + force-graph)
      </footer>
    </aside>
  );
}


// ─── TopicNode (재사용) ───────────────────────────────────────
// 토픽 항목 1개 — chevron + 라벨 + (펼침 시) item sub-list.
// 카테고리 child / orphan 양쪽에서 재사용.

interface TopicNodeProps {
  topic: GraphNodeData;
  items: GraphNodeData[];
  selectedNodeFullId: string | null;
  relatedIds?: Set<string>;
  isOpen: boolean;
  onToggle: () => void;
  onNodeSelect: (fullId: string) => void;
  itemsLabel: string;
  showSlugInline?: boolean;
}

function TopicNode({
  topic,
  items,
  selectedNodeFullId,
  relatedIds,
  isOpen,
  onToggle,
  onNodeSelect,
  itemsLabel,
  showSlugInline = false,
}: TopicNodeProps) {
  const isSelected = selectedNodeFullId === topic.id;
  const isRelated = !isSelected && (relatedIds?.has(topic.id) ?? false);
  const tDot = topicKindColor(topic.primary_external_id);
  const hasItemsInGraph = items.length > 0;
  const totalItems = topic.item_count ?? items.length ?? 0;

  return (
    <li data-tree-id={topic.id}>
      <div
        className={`flex items-center rounded ${
          isSelected
            ? "bg-orange-100 dark:bg-orange-900/30 ring-2 ring-orange-400"
            : isRelated
              ? "bg-amber-50 dark:bg-amber-900/20"
              : "hover:bg-zinc-100 dark:hover:bg-zinc-800"
        }`}
      >
        <button
          type="button"
          onClick={onToggle}
          className="px-1 py-1 text-[10px] text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100"
          aria-label={isOpen ? "collapse" : "expand"}
          disabled={!hasItemsInGraph}
          title={
            hasItemsInGraph
              ? isOpen ? "접기" : "펼치기"
              : "이 토픽을 먼저 클릭하면 자료들이 그래프에 로드됩니다"
          }
        >
          {hasItemsInGraph ? (isOpen ? "▾" : "▸") : "·"}
        </button>
        <button
          type="button"
          onClick={() => onNodeSelect(topic.id)}
          className={`flex-1 text-left px-1 py-1 text-[11px] transition flex items-start gap-2 ${
            isSelected
              ? "text-orange-700 dark:text-orange-300 font-medium"
              : isRelated
                ? "text-amber-800 dark:text-amber-200"
                : "text-zinc-700 dark:text-zinc-300"
          }`}
          title={topic.slug || ""}
        >
          <span
            className="mt-0.5 inline-block w-2 h-2 rounded-full shrink-0"
            style={{ backgroundColor: tDot }}
          />
          <span className="flex-1 min-w-0">
            <span className="block truncate">{topic.label}</span>
            <span className="block text-[10px] text-zinc-500">
              {totalItems} {itemsLabel}
              {showSlugInline && topic.slug ? ` · ${topic.slug}` : ""}
            </span>
          </span>
        </button>
      </div>
      {isOpen && items.length > 0 && (
        <ul className="ml-6 mt-0.5 mb-1 space-y-0.5 border-l border-zinc-200 dark:border-zinc-800 pl-2">
          {items.map((it) => {
            const itemSel = selectedNodeFullId === it.id;
            const itemRel = !itemSel && (relatedIds?.has(it.id) ?? false);
            const iDot = sourceTypeColor(it.source_type);
            return (
              <li key={it.id} data-tree-id={it.id}>
                <button
                  type="button"
                  onClick={() => onNodeSelect(it.id)}
                  className={`w-full text-left px-2 py-1 rounded text-[10px] transition flex items-start gap-2 ${
                    itemSel
                      ? "bg-orange-100 dark:bg-orange-900/30 text-orange-700 dark:text-orange-300 font-medium ring-2 ring-orange-400"
                      : itemRel
                        ? "bg-amber-50 dark:bg-amber-900/20 text-amber-800 dark:text-amber-200"
                        : "hover:bg-zinc-100 dark:hover:bg-zinc-800 text-zinc-700 dark:text-zinc-300"
                  }`}
                  title={it.source_url || ""}
                >
                  <span
                    className="mt-0.5 inline-block w-1.5 h-1.5 rounded-sm shrink-0"
                    style={{ backgroundColor: iDot }}
                  />
                  <span className="flex-1 min-w-0">
                    <span className="block truncate">
                      {it.label || it.title || "(no title)"}
                    </span>
                    <span className="block text-[9px] text-zinc-500">
                      {it.source_type}
                      {it.has_notes ? " · 📝" : ""}
                      {it.is_read === false ? " · ●" : ""}
                    </span>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </li>
  );
}
