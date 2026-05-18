"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import GraphView from "@/components/GraphView";
import ItemDetails from "@/components/ItemDetails";
import Legend from "@/components/Legend";
import NodeDetails from "@/components/NodeDetails";
import TopicsTree from "@/components/TopicsTree";
import {
  expandGraphCategory,
  expandGraphTopic,
  getGraphCategories,
  getGraphTopics,
  getItemNeighborhood,
  searchGraph,
} from "@/lib/api";
import { useT } from "@/lib/i18n/context";
import type { GraphResponse } from "@/types/graph";

const EMPTY: GraphResponse = { nodes: [], edges: [] };

// 두 그래프 응답을 union (사용자 요구: 클릭 시 누적, 유니온 스테이션 hub-spoke).
// dedup 은 node.data.id / edge.data.id 기준 — backend 가 같은 id 일관 발행.
function mergeGraph(prev: GraphResponse, add: GraphResponse): GraphResponse {
  const seenNodes = new Set(prev.nodes.map((n) => n.data.id));
  const seenEdges = new Set(prev.edges.map((e) => e.data.id));
  return {
    nodes: [
      ...prev.nodes,
      ...add.nodes.filter((n) => !seenNodes.has(n.data.id)),
    ],
    edges: [
      ...prev.edges,
      ...add.edges.filter((e) => !seenEdges.has(e.data.id)),
    ],
  };
}

// navigation history — graph 상태 + 선택 노드 snapshot
interface HistoryFrame {
  graph: GraphResponse;
  selectedNodeFullId: string | null;
  selectedItemId: string | null;
  isSubsetView: boolean;
}

export default function HomePage() {
  const { t, locale } = useT();
  const [graph, setGraph] = useState<GraphResponse>(EMPTY);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedItemId, setSelectedItemId] = useState<string | null>(null);
  const [selectedNodeFullId, setSelectedNodeFullId] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [isSubsetView, setIsSubsetView] = useState(false);
  const [viewMode, setViewMode] = useState<"categories" | "topics">("categories");
  // history stack — push 시점은 graph 가 바뀌기 *직전* 의 상태
  const [history, setHistory] = useState<HistoryFrame[]>([]);

  const pushHistory = useCallback(() => {
    setHistory((prev) => [
      ...prev,
      {
        graph,
        selectedNodeFullId,
        selectedItemId,
        isSubsetView,
      },
    ]);
  }, [graph, selectedNodeFullId, selectedItemId, isSubsetView]);

  const loadAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const g =
        viewMode === "categories"
          ? await getGraphCategories(500)
          : await getGraphTopics(5000);
      setGraph(g);
      setSelectedNodeFullId(null);
      setSelectedItemId(null);
      setIsSubsetView(false);
      setHistory([]); // 전체 복귀 시 history 초기화
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [viewMode]);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  const handleSearchSubmit = useCallback(
    async (q: string) => {
      if (!q.trim()) {
        await loadAll();
        return;
      }
      pushHistory();
      setLoading(true);
      setError(null);
      try {
        const g = await searchGraph(q, 50);
        setGraph(g);
        setIsSubsetView(true);
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setLoading(false);
      }
    },
    [loadAll, pushHistory],
  );

  // graph 안에 그 토픽의 item 노드가 이미 있는지 — edges 로 검사
  const topicHasItemsInGraph = useCallback(
    (topicFullId: string): boolean =>
      graph.edges.some(
        (e) =>
          e.data.target === topicFullId && e.data.source.startsWith("item:"),
      ),
    [graph.edges],
  );

  // graph 안에 그 카테고리의 topic 노드가 이미 있는지
  const categoryHasTopicsInGraph = useCallback(
    (categoryFullId: string): boolean =>
      graph.edges.some(
        (e) =>
          e.data.source === categoryFullId && e.data.target.startsWith("topic:"),
      ),
    [graph.edges],
  );

  const handleNodeClick = useCallback(
    async (nodeId: string, type: "topic" | "item" | "category") => {
      setSelectedNodeFullId(nodeId);
      // 사용자 요구 (유니온 스테이션 흐름): 클릭 시 graph 교체 X — 그 노드의 친구들을
      // 기존 그래프에 union 으로 추가. 이미 있으면 fetch 안 하고 highlight 만.
      if (type === "item") {
        const itemUuid = nodeId.replace(/^item:/, "");
        setSelectedItemId(itemUuid);
        try {
          const g = await getItemNeighborhood(itemUuid);
          if (g.nodes.length > 0) {
            pushHistory();
            setGraph((prev) => mergeGraph(prev, g));
            setIsSubsetView(true);
          }
        } catch (e) {
          setError((e as Error).message);
        }
      } else if (type === "category") {
        if (categoryHasTopicsInGraph(nodeId)) return;
        const node = graph.nodes.find((n) => n.data.id === nodeId);
        const slug = node?.data.slug;
        if (!slug) return;
        try {
          const g = await expandGraphCategory(slug);
          if (g.nodes.length > 0) {
            pushHistory();
            setGraph((prev) => mergeGraph(prev, g));
            setIsSubsetView(true);
          }
        } catch (e) {
          setError((e as Error).message);
        }
      } else {
        setSelectedItemId(null);
        if (topicHasItemsInGraph(nodeId)) return;
        const topicUuid = nodeId.replace(/^topic:/, "");
        try {
          const g = await expandGraphTopic(topicUuid);
          if (g.nodes.length > 0) {
            pushHistory();
            setGraph((prev) => mergeGraph(prev, g));
            setIsSubsetView(true);
          }
        } catch (e) {
          setError((e as Error).message);
        }
      }
    },
    [graph.nodes, pushHistory, categoryHasTopicsInGraph, topicHasItemsInGraph],
  );

  const handleSidebarSelect = useCallback(
    (fullId: string) => {
      setSelectedNodeFullId(fullId);
      setSelectedItemId(null);
      // 사용자 보고: 그래프 컨텍스트 유지가 중요. graph 에 이미 그 노드의 친구들이
      // 보이면 fetch 안 하고 highlight 만 (selectedNodeFullId 변경 → relatedIds 자동 강조).
      // graph 에 없으면 expand fetch.
      // item 클릭 — ItemDetails 자동 expand + graph 에 이미 있으면 highlight 만
      if (fullId.startsWith("item:")) {
        const itemUuid = fullId.replace(/^item:/, "");
        setSelectedItemId(itemUuid);
        // graph 에 그 item 노드 있는지 확인
        const inGraph = graph.nodes.some((n) => n.data.id === fullId);
        if (inGraph) return;
        // 없으면 neighborhood fetch + union
        getItemNeighborhood(itemUuid)
          .then((g) => {
            if (g.nodes.length > 0) {
              pushHistory();
              setGraph((prev) => mergeGraph(prev, g));
              setIsSubsetView(true);
            }
          })
          .catch((e) => setError((e as Error).message));
        return;
      }
      if (fullId.startsWith("category:")) {
        if (categoryHasTopicsInGraph(fullId)) return;
        const node = graph.nodes.find((n) => n.data.id === fullId);
        const slug = node?.data.slug;
        if (!slug) return;
        expandGraphCategory(slug)
          .then((g) => {
            if (g.nodes.length > 0) {
              pushHistory();
              setGraph((prev) => mergeGraph(prev, g));
              setIsSubsetView(true);
            }
          })
          .catch((e) => setError((e as Error).message));
      } else if (fullId.startsWith("topic:")) {
        if (topicHasItemsInGraph(fullId)) return;
        const topicUuid = fullId.replace(/^topic:/, "");
        expandGraphTopic(topicUuid)
          .then((g) => {
            if (g.nodes.length > 0) {
              pushHistory();
              setGraph((prev) => mergeGraph(prev, g));
              setIsSubsetView(true);
            }
          })
          .catch((e) => setError((e as Error).message));
      }
    },
    [
      graph.nodes,
      pushHistory,
      topicHasItemsInGraph,
      categoryHasTopicsInGraph,
    ],
  );

  const handleReturnToPrevious = useCallback(() => {
    setHistory((prev) => {
      if (prev.length === 0) return prev;
      const last = prev[prev.length - 1];
      setGraph(last.graph);
      setSelectedNodeFullId(last.selectedNodeFullId);
      setSelectedItemId(last.selectedItemId);
      setIsSubsetView(last.isSubsetView);
      return prev.slice(0, -1);
    });
  }, []);

  // category fullId → slug — NodeDetails 의 detail fetch 용
  const resolveCategorySlug = useCallback(
    (categoryFullId: string): string | null => {
      const node = graph.nodes.find((n) => n.data.id === categoryFullId);
      return node?.data.slug || null;
    },
    [graph.nodes],
  );

  // selected 노드 + 같은 topic 묶음의 모든 친구 노드들 (양방향 highlight).
  // - selected 가 topic: 그 topic 자체 + topic 의 모든 item
  // - selected 가 item:  그 item 이 속한 topic(s) + 그 topic 의 다른 모든 item
  // - selected 가 category: 그 자체 + (graph 안에 있는) 그 카테고리의 topic 들
  const relatedIds = useMemo<Set<string>>(() => {
    const out = new Set<string>();
    if (!selectedNodeFullId) return out;
    out.add(selectedNodeFullId);

    const edges = graph.edges.map((e) => e.data);
    if (selectedNodeFullId.startsWith("topic:")) {
      // 해당 topic 의 모든 item 추가
      for (const e of edges) {
        if (e.target === selectedNodeFullId && e.source.startsWith("item:")) {
          out.add(e.source);
        }
      }
    } else if (selectedNodeFullId.startsWith("item:")) {
      // 1) 이 item 의 모든 topic 추가
      const myTopics: string[] = [];
      for (const e of edges) {
        if (e.source === selectedNodeFullId && e.target.startsWith("topic:")) {
          out.add(e.target);
          myTopics.push(e.target);
        }
      }
      // 2) 그 topic 들의 다른 모든 item 추가
      for (const e of edges) {
        if (myTopics.includes(e.target) && e.source.startsWith("item:")) {
          out.add(e.source);
        }
      }
    } else if (selectedNodeFullId.startsWith("category:")) {
      for (const e of edges) {
        if (e.source === selectedNodeFullId && e.target.startsWith("topic:")) {
          out.add(e.target);
        }
      }
    }
    return out;
  }, [selectedNodeFullId, graph.edges]);

  // NodeDetails 안의 자료/토픽 카드 클릭 (raw uuid 들어옴) → 선택 + 카메라 zoom
  const handleItemClickFromPanel = useCallback((itemUuid: string) => {
    setSelectedItemId(itemUuid);
    setSelectedNodeFullId(`item:${itemUuid}`);
  }, []);
  const handleTopicClickFromPanel = useCallback((topicUuid: string) => {
    setSelectedNodeFullId(`topic:${topicUuid}`);
    setSelectedItemId(null);
  }, []);

  return (
    <div className="flex h-full">
      <TopicsTree
        data={graph}
        selectedNodeFullId={selectedNodeFullId}
        relatedIds={relatedIds}
        onNodeSelect={handleSidebarSelect}
        onSearchSubmit={handleSearchSubmit}
        searchQuery={searchQuery}
        onSearchChange={setSearchQuery}
        isSubsetView={isSubsetView}
        onReturnToAll={() => {
          setSearchQuery("");
          void loadAll();
        }}
        onReturnToPrevious={handleReturnToPrevious}
        historyDepth={history.length}
      />

      <main className="flex-1 h-full relative">
        <GraphView
          data={graph}
          onNodeClick={handleNodeClick}
          selectedId={selectedNodeFullId}
          relatedIds={relatedIds}
        />

        <div className="absolute top-3 left-3 z-10 pointer-events-none flex flex-col gap-1.5">
          {/* view mode toggle */}
          <div className="pointer-events-auto inline-flex bg-white/85 dark:bg-zinc-900/85 backdrop-blur rounded shadow-sm overflow-hidden text-[11px]">
            <button
              type="button"
              onClick={() => setViewMode("categories")}
              className={`px-2.5 py-1 transition-colors ${
                viewMode === "categories"
                  ? "bg-orange-500 text-white"
                  : "text-zinc-700 dark:text-zinc-300 hover:bg-orange-100 dark:hover:bg-orange-900/30"
              }`}
              title={locale === "ko" ? "키워드 카테고리 → 토픽 → 자료" : "categories → topics → items"}
            >
              {locale === "ko" ? "카테고리" : "Categories"}
            </button>
            <button
              type="button"
              onClick={() => setViewMode("topics")}
              className={`px-2.5 py-1 transition-colors ${
                viewMode === "topics"
                  ? "bg-orange-500 text-white"
                  : "text-zinc-700 dark:text-zinc-300 hover:bg-orange-100 dark:hover:bg-orange-900/30"
              }`}
              title={locale === "ko" ? "토픽 + 자료 (전체)" : "topics + items (all)"}
            >
              {locale === "ko" ? "토픽" : "Topics"}
            </button>
          </div>
          {/* 통계 */}
          <div className="pointer-events-auto text-xs px-2 py-1 bg-white/80 dark:bg-zinc-900/80 backdrop-blur rounded shadow-sm">
            {loading ? (
              <span className="text-zinc-500">{t.common.loading}</span>
            ) : error ? (
              <span className="text-red-500">{t.common.error}: {error}</span>
            ) : (
              <span className="text-zinc-700 dark:text-zinc-300">
                {isSubsetView && (
                  <span className="text-orange-600 dark:text-orange-400 font-medium mr-1">
                    {t.graph.subsetView} ·
                  </span>
                )}
                {(() => {
                  const catN = graph.nodes.filter((n) => n.data.type === "category").length;
                  const topN = graph.nodes.filter((n) => n.data.type === "topic").length;
                  const itemN = graph.nodes.filter((n) => n.data.type === "item").length;
                  const parts: string[] = [];
                  if (catN > 0) parts.push(`${catN} categories`);
                  if (topN > 0) parts.push(`${topN} ${t.graph.topics}`);
                  if (itemN > 0) parts.push(`${itemN} ${t.graph.items}`);
                  parts.push(`${graph.edges.length} ${t.graph.edges}`);
                  return parts.join(" · ");
                })()}
              </span>
            )}
          </div>
          {/* 범례 — 통계 라벨 아래 inline 배치 (사용자 요구) */}
          <Legend />
        </div>
      </main>

      <NodeDetails
        selectedNodeFullId={selectedNodeFullId}
        resolveCategorySlug={resolveCategorySlug}
        onItemClick={handleItemClickFromPanel}
        onTopicClick={handleTopicClickFromPanel}
      />

      <ItemDetails
        itemId={selectedItemId}
        onClose={() => {
          setSelectedItemId(null);
          setSelectedNodeFullId(null);
        }}
      />
    </div>
  );
}
