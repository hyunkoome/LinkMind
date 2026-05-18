"use client";

import Link from "next/link";
import { useState } from "react";

import { searchSemantic } from "@/lib/api";
import type { SearchHit, SearchResponse } from "@/types/graph";

// graph UI 의 source_type 색상과 일관성
const SOURCE_COLOR: Record<string, string> = {
  pdf: "bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-300",
  url: "bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300",
  github: "bg-purple-100 dark:bg-purple-900/30 text-purple-700 dark:text-purple-300",
  youtube: "bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-300",
  youtube_playlist: "bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-300",
  arxiv: "bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-300",
  document: "bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300",
  telegram: "bg-cyan-100 dark:bg-cyan-900/30 text-cyan-700 dark:text-cyan-300",
  slack: "bg-violet-100 dark:bg-violet-900/30 text-violet-700 dark:text-violet-300",
};

export default function SearchPage() {
  const [query, setQuery] = useState("");
  const [topK, setTopK] = useState(20);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [response, setResponse] = useState<SearchResponse | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim() || pending) return;
    setPending(true);
    setError(null);
    try {
      const r = await searchSemantic({ query: query.trim(), top_k: topK });
      setResponse(r);
    } catch (e) {
      setError((e as Error).message);
      setResponse(null);
    } finally {
      setPending(false);
    }
  };

  return (
    <main className="h-full overflow-y-auto p-6 max-w-4xl mx-auto w-full">
      <h1 className="text-xl font-semibold mb-1">Search</h1>
      <p className="text-sm text-zinc-500 mb-6">
        Qdrant 의미 검색 (벡터) — 동의어 / paraphrase 도 매칭. 빠른 graph subset
        은 Graph 페이지의 사이드바 검색 (Postgres FTS) 사용.
      </p>

      <form onSubmit={submit} className="mb-6 flex gap-2">
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="예: 포인트클라우드 압축, attention mechanism, 3D Gaussian Splatting…"
          className="flex-1 px-3 py-2 text-sm bg-white dark:bg-zinc-900 border border-zinc-300 dark:border-zinc-700 rounded focus:outline-none focus:ring-1 focus:ring-orange-500"
          disabled={pending}
        />
        <select
          value={topK}
          onChange={(e) => setTopK(Number(e.target.value))}
          className="px-2 py-2 text-xs bg-white dark:bg-zinc-900 border border-zinc-300 dark:border-zinc-700 rounded"
          title="top_k"
        >
          {[5, 10, 20, 50, 100].map((n) => (
            <option key={n} value={n}>
              top {n}
            </option>
          ))}
        </select>
        <button
          type="submit"
          disabled={pending || !query.trim()}
          className="px-4 py-2 text-sm bg-orange-500 hover:bg-orange-600 text-white rounded disabled:opacity-50"
        >
          {pending ? "검색 중…" : "Search"}
        </button>
      </form>

      {error && (
        <div className="mb-4 text-sm text-red-500">에러: {error}</div>
      )}

      {response && (
        <div className="space-y-3">
          <div className="text-xs text-zinc-500">
            <span className="font-mono">{response.query}</span> 결과 {response.hits.length} 건
          </div>
          {response.hits.length === 0 && !pending && (
            <div className="text-sm text-zinc-500 py-8 text-center">
              매칭되는 자료 없음. 키워드를 바꾸거나 더 많은 자료를 ingest 하세요.
            </div>
          )}
          <ul className="space-y-3">
            {response.hits.map((hit) => (
              <HitCard key={`${hit.item_id}:${hit.chunk_id || "-"}`} hit={hit} />
            ))}
          </ul>
        </div>
      )}
    </main>
  );
}

function HitCard({ hit }: { hit: SearchHit }) {
  const apiBase = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";
  const color =
    SOURCE_COLOR[hit.source_type] ||
    "bg-zinc-100 dark:bg-zinc-800 text-zinc-700 dark:text-zinc-300";
  const url = hit.source_url
    ? hit.source_url.startsWith("/")
      ? `${apiBase}${hit.source_url}`
      : hit.source_url
    : null;
  return (
    <li className="bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 rounded p-3">
      <div className="flex items-center gap-2 mb-1">
        <span className={`text-[10px] px-1.5 py-0.5 rounded ${color}`}>
          {hit.source_type}
        </span>
        <span className="text-[10px] text-zinc-400 font-mono">
          score {hit.score.toFixed(3)}
        </span>
        <Link
          href={`/?item=${hit.item_id}`}
          className="ml-auto text-[10px] text-orange-600 dark:text-orange-400 hover:underline"
        >
          → graph 에서 보기
        </Link>
      </div>
      <h3 className="text-sm font-medium break-words mb-1">
        {hit.title || "(no title)"}
      </h3>
      {hit.summary && (
        <p className="text-xs text-zinc-600 dark:text-zinc-400 line-clamp-3 mb-1.5">
          {hit.summary}
        </p>
      )}
      {hit.snippet && !hit.summary && (
        <p className="text-xs text-zinc-500 italic line-clamp-2 mb-1.5">
          …{hit.snippet}…
        </p>
      )}
      <div className="flex items-center justify-between gap-2 flex-wrap">
        {url && (
          <a
            href={url}
            target="_blank"
            rel="noreferrer noopener"
            className="text-[11px] text-blue-600 dark:text-blue-400 hover:underline break-all"
          >
            {url}
          </a>
        )}
        {hit.tags.length > 0 && (
          <div className="flex gap-1 flex-wrap">
            {hit.tags.slice(0, 8).map((tag) => (
              <span
                key={tag}
                className="text-[9px] px-1 py-0.5 bg-zinc-100 dark:bg-zinc-800 rounded text-zinc-600 dark:text-zinc-400"
              >
                {tag}
              </span>
            ))}
          </div>
        )}
      </div>
    </li>
  );
}
