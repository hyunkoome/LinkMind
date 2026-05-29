"use client";

import { useMemo, useState } from "react";

import { expandGraphKeyword } from "@/lib/api";
import { KEYWORD_COLOR, wikiKindColor } from "@/lib/colors";
import { useT } from "@/lib/i18n/context";
import type { GraphNodeData, GraphResponse } from "@/types/graph";

// D10.5 세션 B — 좌측 트리: 키워드 ▸ (co-occur) 하위 키워드 ▸ … ▸ 위키.
// 키워드를 펼치면 expandGraphKeyword 로 "같은 위키를 공유하는 키워드(하위)" + "그
// 키워드의 위키"를 실시간 로드 (동적). 순환(ai▸ml▸ai)은 조상 추적으로 방지.
// 위키 = 엔드포인트 — 클릭 시 onNodeSelect("wiki:<slug>") → 중앙 본문 + 우측 그래프.

interface TopicsTreeProps {
  data: GraphResponse; // 최상단 키워드 (getGraphKeywords)
  selectedNodeFullId: string | null;
  relatedIds?: Set<string>; // (미사용 — 호환 위해 유지)
  onNodeSelect: (fullId: string) => void;
  onSearchSubmit: (query: string) => void;
  searchQuery: string;
  onSearchChange: (q: string) => void;
  isSubsetView: boolean;
  onReturnToAll: () => void;
  onReturnToPrevious?: () => void;
  historyDepth?: number;
}

export default function TopicsTree({
  data,
  selectedNodeFullId,
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

  const topKeywords = useMemo(
    () => data.nodes.filter((n) => n.data.type === "keyword").map((n) => n.data),
    [data],
  );

  // 검색 — 최상단 키워드 client-side 필터 (label 부분 매칭).
  const q = searchQuery.trim().toLowerCase();
  const shown = useMemo(
    () => (q ? topKeywords.filter((k) => (k.label || "").toLowerCase().includes(q)) : topKeywords),
    [topKeywords, q],
  );

  return (
    <aside className="w-full h-full overflow-hidden flex flex-col border-r border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900">
      <header className="p-3 border-b border-zinc-200 dark:border-zinc-800">
        <h1 className="text-base font-semibold mb-2 text-orange-600 dark:text-orange-400">
          {t.app.title}
        </h1>

        {isSubsetView && (
          <div className="flex gap-1 mb-2">
            {onReturnToPrevious && historyDepth > 0 && (
              <button
                type="button"
                onClick={onReturnToPrevious}
                className="flex-1 px-2 py-2 text-xs bg-zinc-200 dark:bg-zinc-700 hover:bg-zinc-300 dark:hover:bg-zinc-600 text-zinc-800 dark:text-zinc-100 rounded font-medium"
              >
                {locale === "ko" ? "← 이전" : "← back"}
              </button>
            )}
            <button
              type="button"
              onClick={onReturnToAll}
              className="flex-1 px-2 py-2 text-xs bg-orange-500 hover:bg-orange-600 text-white rounded font-medium shadow"
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
            placeholder={locale === "ko" ? "키워드 검색…" : "search keywords…"}
            className="w-full px-2 py-1.5 text-sm bg-zinc-100 dark:bg-zinc-800 border border-zinc-300 dark:border-zinc-700 rounded focus:outline-none focus:ring-1 focus:ring-orange-500"
          />
        </form>
      </header>

      <div className="flex-1 overflow-y-auto p-2">
        <div className="text-[10px] uppercase tracking-wider text-zinc-500 mb-1 px-1">
          {locale === "ko" ? "키워드" : "Keywords"} ({shown.length}
          {q ? "" : topKeywords.length >= 300 ? "+" : ""})
        </div>
        {shown.length === 0 ? (
          <div className="text-xs text-zinc-500 px-2 py-3">
            {t.topicsTree.empty}
          </div>
        ) : (
          <ul className="space-y-0.5">
            {shown.map((kw) => (
              <KeywordNode
                key={kw.id}
                keyword={kw}
                depth={0}
                ancestors={EMPTY_ANCESTORS}
                selectedNodeFullId={selectedNodeFullId}
                onNodeSelect={onNodeSelect}
                itemsLabel={t.topicsTree.itemsCount}
              />
            ))}
          </ul>
        )}
      </div>

      <footer className="p-2 text-[10px] text-zinc-400 border-t border-zinc-200 dark:border-zinc-800">
        키워드 ▸ 하위 키워드 ▸ … ▸ 위키
      </footer>
    </aside>
  );
}

const EMPTY_ANCESTORS: ReadonlySet<string> = new Set();
// 키워드 트리 최대 깊이 — co-occurrence 는 본질상 무한 연결이라 깊이 제한 필수.
// depth 0,1,2 까지만 하위 키워드, 그 이상(끝)은 위키(엔드포인트)만 보임.
const MAX_KEYWORD_DEPTH = 3;

// ─── KeywordNode (재귀, lazy) ────────────────────────────────
// 키워드 1개 — 펼치면 co-occur 하위 키워드 + 위키 를 실시간 로드.

interface KeywordNodeProps {
  keyword: GraphNodeData;
  depth: number; // 0 = 최상단. MAX_KEYWORD_DEPTH 도달 시 하위 키워드 X (위키만).
  ancestors: ReadonlySet<string>; // 조상 키워드 slug (순환 방지)
  selectedNodeFullId: string | null;
  onNodeSelect: (fullId: string) => void;
  itemsLabel: string;
}

function KeywordNode({
  keyword,
  depth,
  ancestors,
  selectedNodeFullId,
  onNodeSelect,
  itemsLabel,
}: KeywordNodeProps) {
  const { locale } = useT();
  const slug = keyword.slug || keyword.label || "";
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [childKeywords, setChildKeywords] = useState<GraphNodeData[] | null>(null);
  const [childWikis, setChildWikis] = useState<GraphNodeData[] | null>(null);

  const childAncestors = useMemo(
    () => new Set([...ancestors, slug]),
    [ancestors, slug],
  );

  const toggle = async () => {
    if (!open && childKeywords === null) {
      setLoading(true);
      try {
        const g = await expandGraphKeyword(slug);
        const kws = g.nodes
          .filter(
            (n) =>
              n.data.type === "keyword" &&
              (n.data.slug || "") !== slug &&
              !ancestors.has(n.data.slug || ""),
          )
          .map((n) => n.data);
        const wikis = g.nodes.filter((n) => n.data.type === "wiki").map((n) => n.data);
        // 레벨당 상위 12개 co-occur 키워드만 (클릭 부담 완화 — backend 가 shared 순 정렬).
        setChildKeywords(kws.slice(0, 12));
        setChildWikis(wikis);
      } catch {
        setChildKeywords([]);
        setChildWikis([]);
      } finally {
        setLoading(false);
      }
    }
    setOpen((o) => !o);
  };

  return (
    <li>
      <button
        type="button"
        onClick={toggle}
        className="flex items-center gap-1.5 w-full text-left px-1 py-1 rounded text-xs hover:bg-zinc-100 dark:hover:bg-zinc-800 text-zinc-700 dark:text-zinc-300"
        title={slug}
      >
        <span className="text-zinc-400 w-3 shrink-0">{open ? "▾" : "▸"}</span>
        <span
          className="inline-block w-2.5 h-2.5 rounded-full shrink-0"
          style={{ backgroundColor: KEYWORD_COLOR }}
        />
        <span className="flex-1 min-w-0 truncate">🔑 {keyword.label}</span>
        <span className="text-[10px] text-zinc-500 shrink-0">
          {keyword.wiki_count ?? 0}
        </span>
      </button>
      {open && (
        <ul className="ml-4 mt-0.5 mb-1 space-y-0.5 border-l border-zinc-200 dark:border-zinc-800 pl-2">
          {loading && (
            <li className="text-[10px] text-zinc-400 px-1 py-0.5">
              {locale === "ko" ? "불러오는 중…" : "loading…"}
            </li>
          )}
          {/* 하위 키워드 (재귀) — 깊이 제한 안에서만 */}
          {depth + 1 < MAX_KEYWORD_DEPTH &&
            childKeywords?.map((k) => (
              <KeywordNode
                key={k.id}
                keyword={k}
                depth={depth + 1}
                ancestors={childAncestors}
                selectedNodeFullId={selectedNodeFullId}
                onNodeSelect={onNodeSelect}
                itemsLabel={itemsLabel}
              />
            ))}
          {/* 위키 (엔드포인트) */}
          {childWikis?.map((w) => {
            const wid = `wiki:${w.slug}`;
            const sel = selectedNodeFullId === wid;
            return (
              <li key={w.id}>
                <button
                  type="button"
                  onClick={() => onNodeSelect(wid)}
                  className={`flex items-start gap-1.5 w-full text-left px-1 py-1 rounded text-[11px] transition ${
                    sel
                      ? "bg-orange-100 dark:bg-orange-900/30 text-orange-700 dark:text-orange-300 font-medium ring-2 ring-orange-400"
                      : "hover:bg-zinc-100 dark:hover:bg-zinc-800 text-zinc-700 dark:text-zinc-300"
                  }`}
                  title={w.slug || ""}
                >
                  <span
                    className="mt-0.5 inline-block w-2 h-2 rounded-sm shrink-0"
                    style={{ backgroundColor: wikiKindColor(w.primary_external_id) }}
                  />
                  <span className="flex-1 min-w-0">
                    <span className="block truncate">📄 {w.label}</span>
                    <span className="block text-[9px] text-zinc-500">
                      {w.item_count ?? 0} {itemsLabel} · {w.slug}
                    </span>
                  </span>
                </button>
              </li>
            );
          })}
          {!loading &&
            childKeywords?.length === 0 &&
            childWikis?.length === 0 && (
              <li className="text-[10px] text-zinc-400 px-1 py-0.5">
                {locale === "ko" ? "연결된 항목 없음" : "no related items"}
              </li>
            )}
        </ul>
      )}
    </li>
  );
}
