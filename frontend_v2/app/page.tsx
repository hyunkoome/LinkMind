"use client";

import { useCallback, useEffect, useState } from "react";

import GraphView from "@/components/GraphView";
import ItemDetails from "@/components/ItemDetails";
import TopicsTree from "@/components/TopicsTree";
import {
  getGraphTopics,
  getItemNeighborhood,
  searchGraph,
} from "@/lib/api";
import type { GraphResponse } from "@/types/graph";

const EMPTY: GraphResponse = { nodes: [], edges: [] };

export default function HomePage() {
  const [graph, setGraph] = useState<GraphResponse>(EMPTY);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedItemId, setSelectedItemId] = useState<string | null>(null);
  const [selectedNodeFullId, setSelectedNodeFullId] = useState<string | null>(null);
  const [selectedTopicId, setSelectedTopicId] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  // 현재 graph 가 전체인지 (false) 검색/이웃 subset 인지 (true) — "전체로 복귀" 버튼 강조용
  const [isSubsetView, setIsSubsetView] = useState(false);

  const loadAllTopics = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const g = await getGraphTopics(100);
      setGraph(g);
      setSelectedTopicId(null);
      setIsSubsetView(false);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  // 초기 — 전체 graph
  useEffect(() => {
    void loadAllTopics();
  }, [loadAllTopics]);

  const handleSearchSubmit = useCallback(
    async (q: string) => {
      if (!q.trim()) {
        await loadAllTopics();
        return;
      }
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
    [loadAllTopics],
  );

  const handleNodeClick = useCallback(
    async (nodeId: string, type: "topic" | "item") => {
      setSelectedNodeFullId(nodeId);
      if (type === "item") {
        const itemUuid = nodeId.replace(/^item:/, "");
        setSelectedItemId(itemUuid);
        // graph 도 노드 중심 view 로 갱신 (이웃 표시)
        try {
          const g = await getItemNeighborhood(itemUuid);
          if (g.nodes.length > 0) {
            setGraph(g);
            setIsSubsetView(true);
          }
        } catch (e) {
          setError((e as Error).message);
        }
      } else {
        // topic 클릭 — sidebar selected 표시
        setSelectedTopicId(nodeId.replace(/^topic:/, ""));
        setSelectedItemId(null);
      }
    },
    [],
  );

  const handleTopicClickFromTree = useCallback((topicNodeId: string) => {
    setSelectedTopicId(topicNodeId);
    setSelectedNodeFullId(`topic:${topicNodeId}`);
  }, []);

  return (
    <div className="flex h-full">
      <TopicsTree
        data={graph}
        selectedTopicId={selectedTopicId}
        onTopicClick={handleTopicClickFromTree}
        onSearchSubmit={handleSearchSubmit}
        searchQuery={searchQuery}
        onSearchChange={setSearchQuery}
      />

      <main className="flex-1 h-full relative">
        <GraphView
          data={graph}
          onNodeClick={handleNodeClick}
          selectedId={selectedNodeFullId}
        />

        {/* graph 상단 toolbar — 통계 + (subset 일 때) prominent 한 '전체로' 버튼 */}
        <div className="absolute top-3 left-3 right-3 flex items-center justify-between gap-2 pointer-events-none">
          <div className="pointer-events-auto text-xs px-2 py-1 bg-white/80 dark:bg-zinc-900/80 backdrop-blur rounded shadow-sm">
            {loading ? (
              <span className="text-zinc-500">loading…</span>
            ) : error ? (
              <span className="text-red-500">error: {error}</span>
            ) : (
              <span className="text-zinc-700 dark:text-zinc-300">
                {isSubsetView && (
                  <span className="text-orange-600 dark:text-orange-400 font-medium mr-1">
                    🔍 부분 view ·
                  </span>
                )}
                {graph.nodes.filter((n) => n.data.type === "topic").length} topics
                {" · "}
                {graph.nodes.filter((n) => n.data.type === "item").length} items
                {" · "}
                {graph.edges.length} edges
              </span>
            )}
          </div>
          <button
            type="button"
            onClick={() => {
              setSearchQuery("");
              setSelectedItemId(null);
              setSelectedNodeFullId(null);
              void loadAllTopics();
            }}
            className={`pointer-events-auto text-xs px-3 py-1.5 backdrop-blur rounded shadow-sm transition ${
              isSubsetView
                ? "bg-orange-500 hover:bg-orange-600 text-white font-medium animate-pulse"
                : "bg-white/80 dark:bg-zinc-900/80 hover:bg-orange-100 dark:hover:bg-orange-900/30"
            }`}
            title={isSubsetView ? "현재 부분 view — 클릭으로 전체 그래프 복귀" : "전체 그래프 새로고침"}
          >
            {isSubsetView ? "← 전체 그래프로 복귀" : "🔄 전체 그래프"}
          </button>
        </div>
      </main>

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
