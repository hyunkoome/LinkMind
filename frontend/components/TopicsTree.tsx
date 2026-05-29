"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { KEYWORD_COLOR, sourceTypeColor, wikiKindColor } from "@/lib/colors";
import { useT } from "@/lib/i18n/context";
import type { GraphResponse, GraphNodeData } from "@/types/graph";

// D10.5 세션 B — 좌측 트리를 keyword ▸ wiki ▸ item 으로 (옛 category ▸ topic ▸ item 폐기).
// 엣지 방향: keyword → wiki (source=keyword), wiki → item (source=wiki).

interface TopicsTreeProps {
  data: GraphResponse;
  /** selectedNodeFullId: "keyword:<kw>" | "wiki:<slug>" | "item:<uuid>" | null */
  selectedNodeFullId: string | null;
  /** selected + 그와 같은 묶음의 모든 노드 fullId. sidebar 부드러운 highlight 에 사용. */
  relatedIds?: Set<string>;
  /** 클릭한 노드의 fullId (e.g. "keyword:<kw>" / "wiki:<slug>") — page.tsx 가 분기 */
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

const LS_EXPANDED = "linkmind:tree-expanded-keywords";

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
    keywords,
    wikisByKeyword,
    itemsByWiki,
    orphanWikis,
    allWikis,
  } = useMemo(() => {
    const kws: GraphNodeData[] = [];
    const wikisMap = new Map<string, GraphNodeData>();
    const itemsMap = new Map<string, GraphNodeData>();
    const orphan: GraphNodeData[] = [];
    for (const n of data.nodes) {
      if (n.data.type === "keyword") kws.push(n.data);
      else if (n.data.type === "wiki") wikisMap.set(n.data.id, n.data);
      else if (n.data.type === "item") itemsMap.set(n.data.id, n.data);
    }
    // keyword→wiki 엣지 + wiki→item 엣지 분리.
    const wikisBy: Record<string, GraphNodeData[]> = {};
    const itemsByWk: Record<string, GraphNodeData[]> = {};
    const assignedWikiIds = new Set<string>();
    for (const e of data.edges) {
      if (e.data.source.startsWith("keyword:") && e.data.target.startsWith("wiki:")) {
        const kid = e.data.source;
        const wiki = wikisMap.get(e.data.target);
        if (!wiki) continue;
        (wikisBy[kid] ||= []).push(wiki);
        assignedWikiIds.add(e.data.target);
      } else if (
        e.data.source.startsWith("wiki:") &&
        e.data.target.startsWith("item:")
      ) {
        const item = itemsMap.get(e.data.target);
        if (!item) continue;
        (itemsByWk[e.data.source] ||= []).push(item);
      }
    }
    for (const w of wikisMap.values()) {
      if (!assignedWikiIds.has(w.id)) orphan.push(w);
    }
    kws.sort((a, b) => (b.wiki_count || 0) - (a.wiki_count || 0));
    Object.values(wikisBy).forEach((list) =>
      list.sort((a, b) => (b.item_count || 0) - (a.item_count || 0)),
    );
    orphan.sort((a, b) => (b.item_count || 0) - (a.item_count || 0));
    return {
      keywords: kws,
      wikisByKeyword: wikisBy,
      itemsByWiki: itemsByWk,
      orphanWikis: orphan,
      allWikis: Array.from(wikisMap.values()).sort(
        (a, b) => (b.item_count || 0) - (a.item_count || 0),
      ),
    };
  }, [data]);

  // 펼침 상태 — keyword 별 (fullId set). localStorage 보존.
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
  const toggleExpanded = (kid: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(kid)) next.delete(kid);
      else next.add(kid);
      try {
        window.localStorage.setItem(LS_EXPANDED, JSON.stringify([...next]));
      } catch {
        /* ignore */
      }
      return next;
    });
  };

  // selectedNodeFullId 가 wiki/item 이고 그 wiki 가 속한 keyword 가 있으면 자동 expand + scroll.
  useEffect(() => {
    if (!selectedNodeFullId) return;
    const candidateWikiIds = new Set<string>();
    if (selectedNodeFullId.startsWith("wiki:")) candidateWikiIds.add(selectedNodeFullId);
    if (relatedIds) {
      for (const id of relatedIds) {
        if (id.startsWith("wiki:")) candidateWikiIds.add(id);
      }
    }
    if (candidateWikiIds.size > 0) {
      const toOpen: string[] = [];
      for (const [kid, list] of Object.entries(wikisByKeyword)) {
        if (list.some((wk) => candidateWikiIds.has(wk.id))) {
          toOpen.push(kid);
        }
      }
      if (toOpen.length > 0) {
        setExpanded((prev) => {
          const next = new Set(prev);
          let changed = false;
          for (const kid of toOpen) {
            if (!next.has(kid)) {
              next.add(kid);
              changed = true;
            }
          }
          return changed ? next : prev;
        });
      }
    }
    requestAnimationFrame(() => {
      const el = containerRef.current?.querySelector<HTMLElement>(
        `[data-tree-id="${selectedNodeFullId}"]`,
      );
      el?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
  }, [selectedNodeFullId, wikisByKeyword, relatedIds]);

  // keyword 노드가 1개 이상이면 트리 모드 (그 외엔 wiki flat list)
  const showKeywordTree = keywords.length > 0;

  return (
    <aside className="w-full h-full overflow-hidden flex flex-col border-r border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900">
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
        {showKeywordTree && (
          <>
            <div className="text-[10px] uppercase tracking-wider text-zinc-500 mb-1 px-1">
              {locale === "ko" ? "키워드" : "Keywords"} ({keywords.length})
            </div>
            <ul className="space-y-0.5 mb-3">
              {keywords.map((kw) => {
                // kw.id 는 fullId ("keyword:<kw>").
                const isOpen = expanded.has(kw.id);
                const isSelected = selectedNodeFullId === kw.id;
                const isRelated = !isSelected && (relatedIds?.has(kw.id) ?? false);
                const childWikis = wikisByKeyword[kw.id] || [];
                return (
                  <li key={kw.id} data-tree-id={kw.id}>
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
                        onClick={() => toggleExpanded(kw.id)}
                        className="px-1 py-1.5 text-xs text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100"
                        aria-label={isOpen ? "collapse" : "expand"}
                        title={
                          childWikis.length > 0
                            ? isOpen ? "접기" : "펼치기"
                            : "이 키워드를 먼저 클릭하면 위키들이 그래프에 로드됩니다"
                        }
                      >
                        {isOpen ? "▾" : "▸"}
                      </button>
                      <button
                        type="button"
                        onClick={() => onNodeSelect(kw.id)}
                        className={`flex-1 text-left px-1 py-1.5 text-xs transition flex items-center gap-2 ${
                          isSelected
                            ? "text-orange-700 dark:text-orange-300 font-medium"
                            : "text-zinc-700 dark:text-zinc-300"
                        }`}
                        title={kw.slug || ""}
                      >
                        <span
                          className="inline-block w-2.5 h-2.5 rounded-full shrink-0"
                          style={{ backgroundColor: KEYWORD_COLOR }}
                        />
                        <span className="flex-1 min-w-0">
                          <span className="block truncate">🔑 {kw.label}</span>
                          <span className="block text-[10px] text-zinc-500">
                            {kw.wiki_count ?? childWikis.length} wikis
                          </span>
                        </span>
                      </button>
                    </div>
                    {isOpen && childWikis.length > 0 && (
                      <ul className="ml-6 mt-0.5 mb-1 space-y-0.5 border-l border-zinc-200 dark:border-zinc-800 pl-2">
                        {childWikis.map((wiki) => (
                          <WikiNode
                            key={wiki.id}
                            wiki={wiki}
                            items={itemsByWiki[wiki.id] || []}
                            selectedNodeFullId={selectedNodeFullId}
                            relatedIds={relatedIds}
                            isOpen={expanded.has(wiki.id)}
                            onToggle={() => toggleExpanded(wiki.id)}
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

        {/* keyword 없는 wiki (또는 keyword 없는 view) */}
        {(showKeywordTree ? orphanWikis : allWikis).length > 0 && (
          <>
            <div className="text-[10px] uppercase tracking-wider text-zinc-500 mb-1 px-1">
              {locale === "ko" ? "위키" : "Wikis"} (
              {(showKeywordTree ? orphanWikis : allWikis).length})
              {showKeywordTree && (
                <span className="ml-1 text-zinc-400">
                  {locale === "ko" ? " · 키워드 없음" : " · no keyword"}
                </span>
              )}
            </div>
            <ul className="space-y-0.5">
              {(showKeywordTree ? orphanWikis : allWikis).map((wiki) => (
                <WikiNode
                  key={wiki.id}
                  wiki={wiki}
                  items={itemsByWiki[wiki.id] || []}
                  selectedNodeFullId={selectedNodeFullId}
                  relatedIds={relatedIds}
                  isOpen={expanded.has(wiki.id)}
                  onToggle={() => toggleExpanded(wiki.id)}
                  onNodeSelect={onNodeSelect}
                  itemsLabel={t.topicsTree.itemsCount}
                  showSlugInline={true}
                />
              ))}
            </ul>
          </>
        )}
        {!showKeywordTree && allWikis.length === 0 && (
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


// ─── WikiNode (재사용) ───────────────────────────────────────
// wiki 항목 1개 — chevron + 라벨 + (펼침 시) item sub-list.
// keyword child / orphan 양쪽에서 재사용.

interface WikiNodeProps {
  wiki: GraphNodeData;
  items: GraphNodeData[];
  selectedNodeFullId: string | null;
  relatedIds?: Set<string>;
  isOpen: boolean;
  onToggle: () => void;
  onNodeSelect: (fullId: string) => void;
  itemsLabel: string;
  showSlugInline?: boolean;
}

function WikiNode({
  wiki,
  items,
  selectedNodeFullId,
  relatedIds,
  isOpen,
  onToggle,
  onNodeSelect,
  itemsLabel,
  showSlugInline = false,
}: WikiNodeProps) {
  const isSelected = selectedNodeFullId === wiki.id;
  const isRelated = !isSelected && (relatedIds?.has(wiki.id) ?? false);
  const wDot = wikiKindColor(wiki.primary_external_id);
  const hasItemsInGraph = items.length > 0;
  const totalItems = wiki.item_count ?? items.length ?? 0;

  return (
    <li data-tree-id={wiki.id}>
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
              : "이 위키를 먼저 클릭하면 자료들이 그래프에 로드됩니다"
          }
        >
          {hasItemsInGraph ? (isOpen ? "▾" : "▸") : "·"}
        </button>
        <button
          type="button"
          onClick={() => onNodeSelect(wiki.id)}
          className={`flex-1 text-left px-1 py-1 text-[11px] transition flex items-start gap-2 ${
            isSelected
              ? "text-orange-700 dark:text-orange-300 font-medium"
              : isRelated
                ? "text-amber-800 dark:text-amber-200"
                : "text-zinc-700 dark:text-zinc-300"
          }`}
          title={wiki.slug || ""}
        >
          <span
            className="mt-0.5 inline-block w-2 h-2 rounded-full shrink-0"
            style={{ backgroundColor: wDot }}
          />
          <span className="flex-1 min-w-0">
            <span className="block truncate">{wiki.label}</span>
            <span className="block text-[10px] text-zinc-500">
              {totalItems} {itemsLabel}
              {showSlugInline && wiki.slug ? ` · ${wiki.slug}` : ""}
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
