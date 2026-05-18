"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef } from "react";

import type { GraphResponse } from "@/types/graph";

// react-force-graph-3d 는 three.js + window 의존 → SSR 비활성화
// 옵시디언 3D Graph community plugin 과 같은 라이브러리.
const ForceGraph3D = dynamic(
  () => import("react-force-graph-3d").then((m) => m.default),
  { ssr: false, loading: () => <GraphLoading /> },
);

interface GraphViewProps {
  data: GraphResponse;
  onNodeClick?: (nodeId: string, type: "topic" | "item") => void;
  selectedId?: string | null;
}

// source_type 별 색상 (옵시디언의 폴더 색상과 같은 역할)
function sourceTypeColor(source: string | undefined): string {
  switch (source) {
    case "pdf": return "#ef4444";          // red
    case "url": return "#3b82f6";          // blue
    case "github": return "#8b5cf6";       // purple
    case "youtube":
    case "youtube_playlist": return "#dc2626"; // dark red
    case "arxiv": return "#10b981";        // green
    case "document": return "#f59e0b";     // amber
    case "telegram": return "#06b6d4";     // cyan
    case "slack": return "#a855f7";        // violet
    default: return "#71717a";             // zinc
  }
}

// react-force-graph 의 노드/링크 포맷 (cytoscape data wrap 없이 평면)
type FGNode = {
  id: string;
  label: string;
  type: "topic" | "item";
  // topic 전용
  slug?: string;
  item_count?: number;
  // item 전용
  source_type?: string;
  source_url?: string | null;
  summary?: string | null;
  tags?: string[];
  is_read?: boolean;
  has_notes?: boolean;
};

type FGLink = {
  source: string;
  target: string;
  role?: string;
};

function GraphLoading() {
  return (
    <div className="w-full h-full flex items-center justify-center text-zinc-500 text-sm">
      3D graph 로딩 중…
    </div>
  );
}

export default function GraphView({
  data,
  onNodeClick,
  selectedId,
}: GraphViewProps) {
  // cytoscape JSON ({data: {...}}) → force-graph 평면 포맷
  const fgData = useMemo(() => {
    const nodes: FGNode[] = data.nodes.map((n) => ({
      id: n.data.id,
      label: n.data.label,
      type: n.data.type,
      slug: n.data.slug,
      item_count: n.data.item_count,
      source_type: n.data.source_type,
      source_url: n.data.source_url,
      summary: n.data.summary,
      tags: n.data.tags,
      is_read: n.data.is_read,
      has_notes: n.data.has_notes,
    }));
    const links: FGLink[] = data.edges.map((e) => ({
      source: e.data.source,
      target: e.data.target,
      role: e.data.role,
    }));
    return { nodes, links };
  }, [data]);

  // graph instance ref — 카메라 제어
  // ForceGraph3D 의 instance API: cameraPosition, zoomToFit, getGraphBbox, ...
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const fgRef = useRef<any>(null);

  // selectedId 변경 → 카메라 줌인
  useEffect(() => {
    if (!selectedId || !fgRef.current) return;
    const fg = fgRef.current;
    const node = fgData.nodes.find((n) => n.id === selectedId) as
      | (FGNode & { x?: number; y?: number; z?: number })
      | undefined;
    if (!node || node.x === undefined) return;
    // 노드 위치 + 약간 떨어져서 카메라 배치
    const distance = 120;
    const distRatio =
      1 + distance / Math.hypot(node.x ?? 1, node.y ?? 1, node.z ?? 1);
    fg.cameraPosition(
      {
        x: (node.x ?? 0) * distRatio,
        y: (node.y ?? 0) * distRatio,
        z: (node.z ?? 0) * distRatio,
      },
      node,
      800,
    );
  }, [selectedId, fgData]);

  const handleNodeClick = useCallback(
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (node: any) => {
      onNodeClick?.(node.id as string, node.type as "topic" | "item");
    },
    [onNodeClick],
  );

  return (
    <div className="w-full h-full bg-zinc-950 relative">
      <ForceGraph3D
        ref={fgRef}
        graphData={fgData}
        // topic 큰 sphere (item_count 비례) / item 작은 sphere (source_type 색상)
        nodeVal={(n) => {
          const node = n as FGNode;
          if (node.type === "topic") {
            return Math.max(4, Math.min(30, (node.item_count || 1) * 2));
          }
          return 2;
        }}
        nodeColor={(n) => {
          const node = n as FGNode;
          if (node.type === "topic") return "#f97316"; // orange
          return sourceTypeColor(node.source_type);
        }}
        nodeLabel={(n) => {
          const node = n as FGNode;
          if (node.type === "topic") {
            return `<div style="background:#27272a;color:#fff;padding:4px 8px;border-radius:4px;font-size:11px;max-width:260px"><b>${node.label}</b><br/><span style="color:#fbbf24">${node.item_count ?? 0} items</span> · ${node.slug || ""}</div>`;
          }
          const noteIcon = node.has_notes ? " 📝" : "";
          const readIcon = node.is_read ? "" : " ●";
          return `<div style="background:#27272a;color:#fff;padding:4px 8px;border-radius:4px;font-size:11px;max-width:260px"><b>${node.label}</b>${readIcon}${noteIcon}<br/><span style="color:#a1a1aa">${node.source_type}${(node.tags?.length || 0) > 0 ? " · " + node.tags!.slice(0, 5).join(", ") : ""}</span></div>`;
        }}
        nodeOpacity={0.95}
        nodeResolution={16}
        linkColor={() => "#cbd5e1"}
        linkOpacity={0.7}
        linkWidth={1.2}
        linkResolution={6}
        linkDirectionalParticles={0}
        linkLabel={(l) => (l as FGLink).role || ""}
        onNodeClick={handleNodeClick}
        backgroundColor="#0a0a0a"
        showNavInfo={false}
        // 약간 더 빠른 cooldown — 초기 안정화 후 정지
        cooldownTime={3000}
        warmupTicks={50}
      />

      <div className="absolute bottom-3 left-3 text-[10px] text-zinc-500 pointer-events-none">
        WebGL 3D · 좌클릭 회전 · 우클릭 pan · 휠 zoom · 노드 click → 이웃 확장
      </div>
    </div>
  );
}
