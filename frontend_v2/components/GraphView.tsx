"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef } from "react"; // useMemo used for fgData below

import { sourceTypeColor, topicKindColor } from "@/lib/colors";
import type { GraphResponse } from "@/types/graph";

// react-force-graph-3d 는 three.js + window 의존 → SSR 비활성화
// 옵시디언 3D Graph community plugin 과 같은 라이브러리.
const ForceGraph3D = dynamic(
  () => import("react-force-graph-3d").then((m) => m.default),
  { ssr: false, loading: () => <GraphLoading /> },
);

interface GraphViewProps {
  data: GraphResponse;
  onNodeClick?: (nodeId: string, type: "topic" | "item" | "category") => void;
  selectedId?: string | null;
  /** selected 와 같은 묶음 (topic + 그 안 items 등) 의 fullId 들. 함께 white 강조. */
  relatedIds?: Set<string>;
}

// react-force-graph 의 노드/링크 포맷 (cytoscape data wrap 없이 평면)
type FGNode = {
  id: string;
  label: string;
  type: "topic" | "item" | "category";
  // topic / category 공통
  slug?: string;
  item_count?: number;
  // topic 전용
  primary_external_id?: Record<string, string>;
  // category 전용
  topic_count?: number;
  color?: string | null;
  pinned?: boolean;
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
  relatedIds,
}: GraphViewProps) {
  // cytoscape JSON ({data: {...}}) → force-graph 평면 포맷
  const fgData = useMemo(() => {
    const nodes: FGNode[] = data.nodes.map((n) => ({
      id: n.data.id,
      label: n.data.label,
      type: n.data.type,
      slug: n.data.slug,
      item_count: n.data.item_count,
      primary_external_id: n.data.primary_external_id,
      topic_count: n.data.topic_count,
      color: n.data.color,
      pinned: n.data.pinned,
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

  // selectedId 변경 → 카메라 zoom-to-node. 노드가 아직 force layout 으로 위치
  // 안 잡혔으면 layout 안정화까지 잠깐 대기 (graph 처음 로드 직후 클릭하는 경우).
  useEffect(() => {
    if (!selectedId || !fgRef.current) return;
    const fg = fgRef.current;

    const tryZoom = (attempt: number) => {
      const node = fgData.nodes.find((n) => n.id === selectedId) as
        | (FGNode & { x?: number; y?: number; z?: number })
        | undefined;
      // 위치 미할당 또는 모두 0 (origin) 인 경우 — layout 진행 중. 잠깐 후 재시도.
      const hasPos =
        node &&
        node.x !== undefined &&
        node.y !== undefined &&
        node.z !== undefined &&
        Math.hypot(node.x, node.y, node.z) > 0.5;
      if (!hasPos) {
        if (attempt < 6) {
          setTimeout(() => tryZoom(attempt + 1), 250);
        }
        return;
      }
      const distance = 120;
      const distRatio =
        1 + distance / Math.hypot(node.x!, node.y!, node.z!);
      fg.cameraPosition(
        {
          x: node.x! * distRatio,
          y: node.y! * distRatio,
          z: node.z! * distRatio,
        },
        node,
        800,
      );
    };
    tryZoom(0);
  }, [selectedId, fgData]);

  const handleNodeClick = useCallback(
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (node: any) => {
      onNodeClick?.(
        node.id as string,
        node.type as "topic" | "item" | "category",
      );
    },
    [onNodeClick],
  );

  // related set 의 효율적 lookup — relatedIds prop 이 비었으면 selected 하나만.
  const isRelated = useCallback(
    (nodeId: string) =>
      nodeId === selectedId || (relatedIds?.has(nodeId) ?? false),
    [selectedId, relatedIds],
  );

  // hex 색을 ratio 만큼 어둡게 (#RRGGBB → 각 channel * (1-ratio)). non-related 노드는
  // 원래 색 정체성 유지하되 시각적으로 흐릿하게 (사용자 요구: 흰색 일괄로 가리지 말 것).
  const darken = (hex: string, ratio: number): string => {
    const m = hex.match(/^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i);
    if (!m) return hex;
    const adj = (n: number) => Math.round(n * (1 - ratio)).toString(16).padStart(2, "0");
    const r = parseInt(m[1], 16);
    const g = parseInt(m[2], 16);
    const b = parseInt(m[3], 16);
    return `#${adj(r)}${adj(g)}${adj(b)}`;
  };

  return (
    <div className="w-full h-full bg-zinc-950 relative">
      <ForceGraph3D
        ref={fgRef}
        graphData={fgData}
        // topic 큰 sphere (item_count 비례) / item 작은 sphere (source_type 색상)
        // selected 노드는 size 1.5x 로 시각 강조
        nodeVal={(n) => {
          const node = n as FGNode;
          let base: number;
          if (node.type === "category") {
            base = Math.max(8, Math.min(50, (node.topic_count || 1) * 4));
          } else if (node.type === "topic") {
            base = Math.max(4, Math.min(30, (node.item_count || 1) * 2));
          } else {
            // item — 토픽보다 명확히 작게 (사용자 요구: 토픽/아이템 시각 구분)
            base = 1.5;
          }
          // 강조 단계 (사용자 피드백): 흰색 일괄로 가리지 말기.
          //   selected (단 하나) : 1.7x + 흰색
          //   related (그 친구들): 1.3x + 원래 색 (정체성 유지)
          //   non-related        : 원래 사이즈 + 어둡게 (visually 흐림)
          if (node.id === selectedId) return base * 1.7;
          if (relatedIds?.has(node.id)) return base * 1.3;
          return base;
        }}
        nodeColor={(n) => {
          const node = n as FGNode;
          // 원래 색상 결정
          const baseColor =
            node.type === "category"
              ? (node.color || (node.pinned ? "#fde047" : "#facc15"))
              : node.type === "topic"
                ? topicKindColor(node.primary_external_id)
                : sourceTypeColor(node.source_type);
          // 사용자 요구: selected + related 다 원래 색 유지 (정체성). non-related 만
          // 어둡게 가라앉힘 (시각적 투명 효과). 어디 선택됐는지는 size 1.7x 로 식별.
          if (node.id === selectedId) return baseColor;
          if (relatedIds?.has(node.id)) return baseColor;
          if (selectedId) return darken(baseColor, 0.65);
          return baseColor;
        }}
        nodeLabel={(n) => {
          const node = n as FGNode;
          if (node.type === "category") {
            const pinIcon = node.pinned ? " 📌" : "";
            return `<div style="background:#27272a;color:#fff;padding:4px 8px;border-radius:4px;font-size:11px;max-width:260px"><b>${node.label}${pinIcon}</b><br/><span style="color:#facc15">${node.topic_count ?? 0} topics · ${node.item_count ?? 0} items</span> · ${node.slug || ""}</div>`;
          }
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
