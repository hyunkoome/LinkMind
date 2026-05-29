"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import WikiBody from "@/components/wiki/WikiBody";
import { expandGraphKeyword, getWikiPage, type WikiPageDetail } from "@/lib/api";
import { useT } from "@/lib/i18n/context";
import type { GraphResponse } from "@/types/graph";

// D10.5 세션 B — 중앙 패널의 keyword/wiki 상세.
//   keyword 선택 → 그 keyword 의 wiki 목록 (클릭 시 onWikiClick)
//   wiki 선택    → 그 wiki 본문 (WikiBody) + keywords + 전체보기 링크
//   item 선택    → ItemDetails 가 담당 (여기는 null)
interface NodeDetailsProps {
  /** "keyword:<kw>" | "wiki:<slug>" | "item:<uuid>" | null */
  selectedNodeFullId: string | null;
  /** wiki sources/그래프 자료 클릭 (raw item uuid) */
  onItemClick: (itemId: string) => void;
  /** keyword 의 wiki 목록에서 클릭 (raw wiki slug) */
  onWikiClick: (wikiSlug: string) => void;
}

export default function NodeDetails({
  selectedNodeFullId,
  onWikiClick,
}: NodeDetailsProps) {
  const { locale } = useT();
  const [kwGraph, setKwGraph] = useState<GraphResponse | null>(null);
  const [wiki, setWiki] = useState<WikiPageDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!selectedNodeFullId || selectedNodeFullId.startsWith("item:")) {
      setKwGraph(null);
      setWiki(null);
      return;
    }
    setLoading(true);
    setError(null);
    setKwGraph(null);
    setWiki(null);
    if (selectedNodeFullId.startsWith("keyword:")) {
      const kw = selectedNodeFullId.replace(/^keyword:/, "");
      expandGraphKeyword(kw)
        .then(setKwGraph)
        .catch((e: Error) => setError(e.message))
        .finally(() => setLoading(false));
    } else if (selectedNodeFullId.startsWith("wiki:")) {
      const slug = selectedNodeFullId.replace(/^wiki:/, "");
      getWikiPage(slug)
        .then(setWiki)
        .catch((e: Error) => setError(e.message))
        .finally(() => setLoading(false));
    } else {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedNodeFullId]);

  // item 이거나 선택 없음 → ItemDetails 가 그 자리에서 동작 (이 패널 비활성)
  if (!selectedNodeFullId || selectedNodeFullId.startsWith("item:")) {
    return null;
  }

  const isKeyword = selectedNodeFullId.startsWith("keyword:");
  const kw = selectedNodeFullId.replace(/^keyword:/, "");
  const wikiSlug = selectedNodeFullId.replace(/^wiki:/, "");
  const wikiNodes = (kwGraph?.nodes ?? []).filter((n) => n.data.type === "wiki");

  return (
    <aside className="flex-1 min-w-0 h-full overflow-y-auto bg-white dark:bg-zinc-900">
      <div className="p-4 space-y-4 max-w-3xl mx-auto w-full">
        {loading && (
          <div className="text-sm text-zinc-500">
            {locale === "ko" ? "불러오는 중…" : "loading…"}
          </div>
        )}
        {error && <div className="text-sm text-red-500">{error}</div>}

        {/* keyword 상세 — 그 keyword 의 wiki 목록 */}
        {!loading && !error && isKeyword && (
          <>
            <div>
              <div className="text-[10px] uppercase tracking-wider text-zinc-500">
                {locale === "ko" ? "키워드" : "Keyword"}
              </div>
              <h2 className="text-lg font-semibold">🔑 {kw}</h2>
            </div>
            <div className="text-[10px] uppercase tracking-wider text-zinc-500">
              {locale === "ko" ? "위키" : "Wikis"} ({wikiNodes.length})
            </div>
            <ul className="space-y-1">
              {wikiNodes.map((w) => (
                <li key={w.data.id}>
                  <button
                    type="button"
                    onClick={() => w.data.slug && onWikiClick(w.data.slug)}
                    className="w-full text-left px-2 py-1.5 rounded text-sm hover:bg-zinc-100 dark:hover:bg-zinc-800 text-zinc-800 dark:text-zinc-200"
                    title={w.data.slug || ""}
                  >
                    <span className="block truncate">{w.data.label}</span>
                    <span className="block text-[10px] text-zinc-500">
                      {w.data.item_count ?? 0} items · {w.data.slug}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </>
        )}

        {/* wiki 상세 — 본문 inline */}
        {!loading && !error && !isKeyword && wiki && (
          <>
            <div className="flex items-center justify-between">
              <div className="text-[10px] uppercase tracking-wider text-zinc-500">
                {locale === "ko" ? "위키" : "Wiki"}
              </div>
              <Link
                href={`/wiki/${encodeURIComponent(wikiSlug)}`}
                className="text-[11px] text-orange-600 dark:text-orange-400 hover:underline"
              >
                {locale === "ko" ? "전체 보기 →" : "open full →"}
              </Link>
            </div>
            <h2 className="text-lg font-semibold break-words">{wiki.title}</h2>
            {wiki.keywords.length > 0 && (
              <div className="flex flex-wrap gap-1">
                {wiki.keywords.map((k) => (
                  <Link
                    key={k}
                    href={`/wiki?keyword=${encodeURIComponent(k)}`}
                    className="text-[10px] px-1.5 py-0.5 rounded bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 hover:bg-blue-200 dark:hover:bg-blue-800/40"
                  >
                    {k}
                  </Link>
                ))}
              </div>
            )}
            {wiki.body ? (
              <WikiBody body={wiki.body} />
            ) : (
              <div className="text-xs text-zinc-500 italic">
                {locale === "ko"
                  ? "아직 본문이 합성되지 않았습니다 (처리 대기/진행 중)."
                  : "wiki body not synthesized yet (queued/in progress)."}
              </div>
            )}
          </>
        )}
      </div>
    </aside>
  );
}
