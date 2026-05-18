"use client";

import { useEffect, useRef } from "react";
import cytoscape, {
  type Core,
  type EventObject,
  type ElementDefinition,
} from "cytoscape";

import type { GraphResponse } from "@/types/graph";

interface GraphViewProps {
  data: GraphResponse;
  onNodeClick?: (nodeId: string, type: "topic" | "item") => void;
  selectedId?: string | null;
}

// topic 색상 = warm orange / item 색상은 source_type 별
function sourceTypeColor(source: string | undefined): string {
  switch (source) {
    case "pdf":
      return "#ef4444"; // red
    case "url":
      return "#3b82f6"; // blue
    case "github":
      return "#8b5cf6"; // purple
    case "youtube":
    case "youtube_playlist":
      return "#dc2626"; // dark red
    case "arxiv":
      return "#10b981"; // green
    case "document":
      return "#f59e0b"; // amber
    case "telegram":
      return "#06b6d4"; // cyan
    case "slack":
      return "#a855f7"; // violet
    default:
      return "#71717a"; // zinc
  }
}

export default function GraphView({
  data,
  onNodeClick,
  selectedId,
}: GraphViewProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | null>(null);

  // 초기화 — 컨테이너 mount 후 한 번
  useEffect(() => {
    if (!containerRef.current) return;
    const cy = cytoscape({
      container: containerRef.current,
      elements: [],
      style: [
        // topic 노드 — 큰 원 (cluster 표현)
        {
          selector: 'node[type = "topic"]',
          style: {
            "background-color": "#f97316",
            "border-color": "#c2410c",
            "border-width": 2,
            label: "data(label)",
            color: "#1f2937",
            "font-size": 11,
            "font-weight": 600,
            "text-valign": "center",
            "text-halign": "center",
            "text-wrap": "wrap",
            "text-max-width": "120px",
            width: "mapData(item_count, 0, 10, 40, 100)",
            height: "mapData(item_count, 0, 10, 40, 100)",
            "text-outline-width": 2,
            "text-outline-color": "#fff7ed",
          },
        },
        // item 노드 — 작은 둥근 사각형, source_type 색상
        {
          selector: 'node[type = "item"]',
          style: {
            "background-color": (ele: cytoscape.NodeSingular) =>
              sourceTypeColor(ele.data("source_type")),
            "border-width": 1,
            "border-color": "#27272a",
            label: "data(label)",
            color: "#fafafa",
            "font-size": 9,
            "text-valign": "center",
            "text-halign": "center",
            "text-wrap": "wrap",
            "text-max-width": "100px",
            shape: "round-rectangle",
            width: 70,
            height: 35,
            "text-outline-width": 1,
            "text-outline-color": "#000",
            "text-outline-opacity": 0.6,
          },
        },
        // 안 읽은 item — 점멸 효과 (border 강조)
        {
          selector: 'node[type = "item"][?is_read = false][!is_read]',
          style: {
            "border-width": 3,
            "border-color": "#facc15",
          },
        },
        // user_notes 있는 item — 작은 인디케이터 (overlay color)
        {
          selector: 'node[type = "item"][?has_notes]',
          style: {
            "border-style": "double",
            "border-width": 4,
          },
        },
        // 선택된 노드
        {
          selector: "node:selected",
          style: {
            "border-color": "#fbbf24",
            "border-width": 5,
            "overlay-color": "#fbbf24",
            "overlay-opacity": 0.15,
            "overlay-padding": 10,
          },
        },
        // 엣지 — 회색 곡선
        {
          selector: "edge",
          style: {
            width: 1.5,
            "line-color": "#a1a1aa",
            "curve-style": "bezier",
            opacity: 0.6,
            label: "data(role)",
            "font-size": 8,
            color: "#52525b",
            "text-background-color": "#fafafa",
            "text-background-opacity": 0.7,
            "text-background-padding": "1px",
          },
        },
      ],
      layout: {
        name: "cose",
        animate: false,
        idealEdgeLength: () => 100,
        nodeOverlap: 20,
        padding: 30,
        randomize: true,
        componentSpacing: 80,
      },
      wheelSensitivity: 0.2,
      minZoom: 0.1,
      maxZoom: 3,
    });

    cy.on("tap", "node", (evt: EventObject) => {
      const node = evt.target;
      const fullId = node.data("id") as string;
      const type = node.data("type") as "topic" | "item";
      onNodeClick?.(fullId, type);
    });

    cyRef.current = cy;

    return () => {
      cy.destroy();
      cyRef.current = null;
    };
    // onNodeClick 은 매 render 다른 ref 일 수도 있어 deps 에 안 넣고 ref 패턴 사용해도
    // 되지만 — 단순화 위해 마운트 1회만. 부모가 GraphView 자체를 unmount/remount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // data 변경 → graph 갱신 (전체 replace + layout 재실행)
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    const elements: ElementDefinition[] = [
      ...data.nodes.map((n) => ({ data: n.data, group: "nodes" as const })),
      ...data.edges.map((e) => ({ data: e.data, group: "edges" as const })),
    ];
    cy.elements().remove();
    cy.add(elements);
    cy.layout({
      name: "cose",
      animate: false,
      idealEdgeLength: () => 100,
      nodeOverlap: 20,
      padding: 30,
      randomize: true,
      componentSpacing: 80,
    }).run();
    cy.fit(undefined, 30);
  }, [data]);

  // selectedId 변경 → 해당 노드 select + center
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy || !selectedId) return;
    cy.nodes().unselect();
    const target = cy.getElementById(selectedId);
    if (target.length > 0) {
      target.select();
      cy.center(target);
    }
  }, [selectedId]);

  return (
    <div
      ref={containerRef}
      className="w-full h-full bg-zinc-100 dark:bg-zinc-900"
    />
  );
}
