"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import GraphView from "@/components/GraphView";
import ItemDetails from "@/components/ItemDetails";
import Legend from "@/components/Legend";
import NodeDetails from "@/components/NodeDetails";
import TopicsTree from "@/components/TopicsTree";
import {
  expandGraphKeyword,
  expandGraphWiki,
  getGraphKeywords,
  getItemNeighborhood,
  searchGraph,
} from "@/lib/api";
import { useT } from "@/lib/i18n/context";
import type { GraphResponse } from "@/types/graph";

const EMPTY: GraphResponse = { nodes: [], edges: [] };

// 중앙(위키) 패널 최소 폭(px) — 좌/우 드래그 시 이만큼은 남김.
const MIN_CENTER = 80;

// navigation history — viewGraph(우측 시각화) + 선택 노드 snapshot
interface HistoryFrame {
  viewGraph: GraphResponse | null;
  selectedNodeFullId: string | null;
  selectedItemId: string | null;
  isSubsetView: boolean;
}

export default function HomePage() {
  const { t, locale } = useT();
  const [graph, setGraph] = useState<GraphResponse>(EMPTY);
  // viewGraph: 우측 그래프 시각화용 subset. null 이면 전체(graph). 트리(좌측)는 항상
  // 전체(graph) 유지 — 그래프만 선택 항목 중심으로 focus (사용자 요구 2026-05-29).
  const [viewGraph, setViewGraph] = useState<GraphResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedItemId, setSelectedItemId] = useState<string | null>(null);
  const [selectedNodeFullId, setSelectedNodeFullId] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [isSubsetView, setIsSubsetView] = useState(false);
  // history stack — push 시점은 graph 가 바뀌기 *직전* 의 상태
  const [history, setHistory] = useState<HistoryFrame[]>([]);

  // 좌/중/우 3분할 패널 폭 (마우스 드래그 리사이즈). 중앙은 flex-1 (나머지 자동).
  // 우측 그래프는 보조라 작게 (기본 280, 최대 600). 좌측 트리 160~480.
  const [leftW, setLeftW] = useState(256);
  const [rightW, setRightW] = useState(200);
  useEffect(() => {
    try {
      const l = window.localStorage.getItem("linkmind:graph-leftW");
      // rightW 는 키 버전업 (graph-rightW2) — 이전에 크게 저장된 값 1회 리셋해 작게 시작.
      const r = window.localStorage.getItem("linkmind:graph-rightW2");
      // 저장된 값 clamp. max 는 화면 기준 동적 — 중앙 MIN_CENTER 만 남도록 허용.
      if (l) setLeftW(Math.max(160, Math.min(window.innerWidth - MIN_CENTER - 180, Number(l))));
      if (r) setRightW(Math.max(120, Math.min(window.innerWidth - MIN_CENTER - 160, Number(r))));
    } catch {
      /* ignore */
    }
  }, []);
  // max 는 동적 — 중앙에 최소 MIN_CENTER 만 남기고 양쪽 거의 끝까지 줄일 수 있게.
  const onLeftResize = useCallback((clientX: number) => {
    const maxL = Math.max(160, window.innerWidth - rightW - MIN_CENTER);
    const w = Math.max(160, Math.min(maxL, clientX));
    setLeftW(w);
    try {
      window.localStorage.setItem("linkmind:graph-leftW", String(w));
    } catch {
      /* ignore */
    }
  }, [rightW]);
  const onRightResize = useCallback((clientX: number) => {
    const maxR = Math.max(120, window.innerWidth - leftW - MIN_CENTER);
    const w = Math.max(120, Math.min(maxR, window.innerWidth - clientX));
    setRightW(w);
    try {
      window.localStorage.setItem("linkmind:graph-rightW2", String(w));
    } catch {
      /* ignore */
    }
  }, [leftW]);

  const pushHistory = useCallback(() => {
    setHistory((prev) => [
      ...prev,
      {
        viewGraph,
        selectedNodeFullId,
        selectedItemId,
        isSubsetView,
      },
    ]);
  }, [viewGraph, selectedNodeFullId, selectedItemId, isSubsetView]);

  const loadAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      // 좌측 트리 진입용 — 빈도 상위 300 (나머지는 검색/펼침으로 도달).
      const g = await getGraphKeywords(300);
      setGraph(g);
      setViewGraph(null);
      setSelectedNodeFullId(null);
      setSelectedItemId(null);
      setIsSubsetView(false);
      setHistory([]); // 전체 복귀 시 history 초기화
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

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
        setViewGraph(null);
        setIsSubsetView(true);
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setLoading(false);
      }
    },
    [loadAll, pushHistory],
  );

  // 선택 항목 중심으로 우측 그래프(viewGraph)를 교체 — union 폐기 (사용자 요구
  // 2026-05-29: 전체가 난잡, 선택 노드가 안 보임). 트리(좌측)는 graph(전체) 그대로
  // 두고 viewGraph 만 그 항목 이웃으로. selectedId → GraphView 가 카메라 zoom + 강조.
  const focusNode = useCallback(
    async (nodeId: string, type: "keyword" | "wiki" | "item") => {
      setSelectedNodeFullId(nodeId);
      try {
        let g: GraphResponse | null = null;
        if (type === "item") {
          const itemUuid = nodeId.replace(/^item:/, "");
          setSelectedItemId(itemUuid);
          g = await getItemNeighborhood(itemUuid);
        } else if (type === "keyword") {
          setSelectedItemId(null);
          g = await expandGraphKeyword(nodeId.replace(/^keyword:/, ""));
        } else {
          setSelectedItemId(null);
          g = await expandGraphWiki(nodeId.replace(/^wiki:/, ""));
        }
        if (g && g.nodes.length > 0) {
          pushHistory();
          setViewGraph(g); // 우측 그래프만 교체 (트리는 전체 유지)
          setIsSubsetView(true);
        }
      } catch (e) {
        setError((e as Error).message);
      }
    },
    [pushHistory],
  );

  // 그래프 노드 클릭 = 트리 선택 = 동일한 focus 동작.
  const handleNodeClick = focusNode;
  const handleSidebarSelect = useCallback(
    (fullId: string) => {
      const type: "keyword" | "wiki" | "item" = fullId.startsWith("item:")
        ? "item"
        : fullId.startsWith("keyword:")
          ? "keyword"
          : "wiki";
      void focusNode(fullId, type);
    },
    [focusNode],
  );

  const handleReturnToPrevious = useCallback(() => {
    setHistory((prev) => {
      if (prev.length === 0) return prev;
      const last = prev[prev.length - 1];
      setViewGraph(last.viewGraph);
      setSelectedNodeFullId(last.selectedNodeFullId);
      setSelectedItemId(last.selectedItemId);
      setIsSubsetView(last.isSubsetView);
      return prev.slice(0, -1);
    });
  }, []);

  // selected 노드 + 같은 묶음의 친구 노드 (양방향 highlight). 엣지 방향:
  //   keyword → wiki (source=keyword, target=wiki), wiki → item (source=wiki, target=item).
  // - selected keyword: 그 keyword 의 wiki 들
  // - selected wiki:    그 wiki 의 item 들 + 부모 keyword
  // - selected item:    그 item 이 속한 wiki 들 + 그 wiki 의 다른 item
  const relatedIds = useMemo<Set<string>>(() => {
    const out = new Set<string>();
    if (!selectedNodeFullId) return out;
    out.add(selectedNodeFullId);

    const edges = (viewGraph ?? graph).edges.map((e) => e.data);
    if (selectedNodeFullId.startsWith("keyword:")) {
      for (const e of edges) {
        if (e.source === selectedNodeFullId && e.target.startsWith("wiki:")) {
          out.add(e.target);
        }
      }
    } else if (selectedNodeFullId.startsWith("wiki:")) {
      for (const e of edges) {
        if (e.source === selectedNodeFullId && e.target.startsWith("item:")) {
          out.add(e.target);
        }
        if (e.target === selectedNodeFullId && e.source.startsWith("keyword:")) {
          out.add(e.source);
        }
      }
    } else if (selectedNodeFullId.startsWith("item:")) {
      // 1) 이 item 이 속한 wiki 들
      const myWikis: string[] = [];
      for (const e of edges) {
        if (e.target === selectedNodeFullId && e.source.startsWith("wiki:")) {
          out.add(e.source);
          myWikis.push(e.source);
        }
      }
      // 2) 그 wiki 들의 다른 item 들
      for (const e of edges) {
        if (myWikis.includes(e.source) && e.target.startsWith("item:")) {
          out.add(e.target);
        }
      }
    }
    return out;
  }, [selectedNodeFullId, viewGraph, graph]);

  // NodeDetails 안의 자료/토픽 카드 클릭 (raw uuid) → 그 항목 중심으로 focus (그래프 교체)
  const handleItemClickFromPanel = useCallback(
    (itemUuid: string) => {
      void focusNode(`item:${itemUuid}`, "item");
    },
    [focusNode],
  );
  const handleWikiClickFromPanel = useCallback(
    (wikiSlug: string) => {
      void focusNode(`wiki:${wikiSlug}`, "wiki");
    },
    [focusNode],
  );

  return (
    <div className="flex h-full">
      {/* 좌측 — 트리 (리사이즈 가능) */}
      <div style={{ width: leftW }} className="shrink-0 h-full overflow-hidden">
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
      </div>
      <ResizeHandle onResize={onLeftResize} />

      {/* 중앙 — 위키/자료 상세 inline (메인 콘텐츠, 크게) */}
      <section className="flex-1 min-w-0 h-full flex">
        {selectedItemId ? (
          <ItemDetails
            itemId={selectedItemId}
            onClose={() => {
              setSelectedItemId(null);
              setSelectedNodeFullId(null);
            }}
          />
        ) : selectedNodeFullId ? (
          <NodeDetails
            selectedNodeFullId={selectedNodeFullId}
            onItemClick={handleItemClickFromPanel}
            onWikiClick={handleWikiClickFromPanel}
          />
        ) : (
          <div className="flex-1 flex items-center justify-center text-sm text-zinc-400 p-6 text-center">
            {locale === "ko"
              ? "오른쪽 그래프에서 노드를 클릭하면 여기에 위키/자료 내용이 표시됩니다."
              : "Click a node in the graph (right) to view its wiki/content here."}
          </div>
        )}
      </section>

      <ResizeHandle onResize={onRightResize} />

      {/* 우측 — 그래프 (보조, 리사이즈 가능) */}
      <aside style={{ width: rightW }} className="shrink-0 h-full relative">
        <GraphView
          data={viewGraph ?? EMPTY}
          onNodeClick={handleNodeClick}
          selectedId={selectedNodeFullId}
          relatedIds={relatedIds}
        />

        {!viewGraph && (
          <div className="absolute inset-0 flex items-center justify-center pointer-events-none px-6">
            <span className="text-zinc-400 dark:text-zinc-500 text-xs text-center leading-relaxed whitespace-pre-line">
              {locale === "ko"
                ? "좌측에서 키워드를 펼쳐 위키를 클릭하면\n여기에 키워드 관계 그래프가 표시됩니다"
                : "Expand a keyword and click a wiki on the left\nto see its keyword-relation graph here"}
            </span>
          </div>
        )}

        <div className="absolute top-3 left-3 z-10 pointer-events-none flex flex-col gap-1.5">
          {/* 통계 — keyword ▸ wiki ▸ item */}
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
                  const g = viewGraph ?? EMPTY;
                  const kwN = g.nodes.filter((n) => n.data.type === "keyword").length;
                  const wikiN = g.nodes.filter((n) => n.data.type === "wiki").length;
                  const itemN = g.nodes.filter((n) => n.data.type === "item").length;
                  const parts: string[] = [];
                  if (kwN > 0) parts.push(`${kwN} keywords`);
                  if (wikiN > 0) parts.push(`${wikiN} wikis`);
                  if (itemN > 0) parts.push(`${itemN} ${t.graph.items}`);
                  parts.push(`${g.edges.length} ${t.graph.edges}`);
                  return parts.join(" · ");
                })()}
              </span>
            )}
          </div>
          {/* 범례 — 통계 라벨 아래 inline 배치 (사용자 요구) */}
          <Legend />
        </div>
      </aside>
    </div>
  );
}

// 좌/중/우 패널 사이 드래그 핸들 — 마우스로 너비 조절. 의존성 없이 자체 구현.
// onResize 는 드래그 중 마우스의 clientX 를 받아 page 가 폭을 계산.
function ResizeHandle({ onResize }: { onResize: (clientX: number) => void }) {
  const draggingRef = useRef(false);
  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (!draggingRef.current) return;
      e.preventDefault();
      onResize(e.clientX);
    };
    const onUp = () => {
      if (!draggingRef.current) return;
      draggingRef.current = false;
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [onResize]);
  return (
    <div
      role="separator"
      aria-orientation="vertical"
      onMouseDown={(e) => {
        e.preventDefault();
        draggingRef.current = true;
        document.body.style.cursor = "col-resize";
        document.body.style.userSelect = "none";
      }}
      className="w-1 shrink-0 h-full cursor-col-resize bg-zinc-200 dark:bg-zinc-800 hover:bg-orange-400 dark:hover:bg-orange-500 transition-colors"
      title="드래그하여 너비 조절"
    />
  );
}
