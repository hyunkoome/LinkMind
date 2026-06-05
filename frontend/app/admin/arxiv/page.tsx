"use client";

import { useCallback, useEffect, useState } from "react";

import {
  addArxivKeyword,
  collectArxiv,
  deleteArxivKeyword,
  listArxivKeywords,
  searchArxiv,
  toggleArxivKeyword,
  type ArxivCollectItem,
  type ArxivPaper,
  type CollectionKeyword,
} from "@/lib/api";
import { useAuth } from "@/lib/auth/context";
import { useT } from "@/lib/i18n/context";

// 키워드 기반 arxiv 수집 (admin 전용, 2026-06-05).
// ① 관심 키워드 관리 ② 키워드/쿼리로 arxiv 검색 미리보기 ③ 골라 수집 → 위키 자동.
export default function ArxivAdminPage() {
  const { locale } = useT();
  const { activeSpace } = useAuth();
  const isAdmin = activeSpace?.role === "owner" || activeSpace?.role === "admin";
  const ko = locale === "ko";

  // ── 키워드 상태 ──
  const [keywords, setKeywords] = useState<CollectionKeyword[]>([]);
  const [newKw, setNewKw] = useState("");
  const [kwError, setKwError] = useState<string | null>(null);

  // ── 검색 상태 ──
  const [query, setQuery] = useState("");
  const [sortBy, setSortBy] = useState("relevance");
  const [maxResults, setMaxResults] = useState(25);
  const [papers, setPapers] = useState<ArxivPaper[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);

  // ── 수집 상태 ──
  const [collecting, setCollecting] = useState(false);
  const [collectLog, setCollectLog] = useState<ArxivCollectItem[]>([]);

  const loadKeywords = useCallback(async () => {
    try {
      const r = await listArxivKeywords();
      setKeywords(r.keywords);
    } catch (e) {
      setKwError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    if (isAdmin) void loadKeywords();
  }, [isAdmin, loadKeywords]);

  if (!isAdmin) {
    return (
      <main className="h-full overflow-y-auto p-6 max-w-4xl mx-auto w-full">
        <h1 className="text-xl font-semibold">{ko ? "arXiv 수집" : "arXiv Harvest"}</h1>
        <div className="text-sm text-zinc-500 mt-4">
          {ko
            ? "이 페이지는 조직 관리자(owner/admin)만 접근할 수 있습니다."
            : "This page is for organization admins only."}
        </div>
      </main>
    );
  }

  const onAddKeyword = async () => {
    const kw = newKw.trim();
    if (!kw) return;
    setKwError(null);
    try {
      await addArxivKeyword(kw);
      setNewKw("");
      await loadKeywords();
    } catch (e) {
      setKwError((e as Error).message);
    }
  };

  const onDeleteKeyword = async (id: string) => {
    try {
      await deleteArxivKeyword(id);
      await loadKeywords();
    } catch (e) {
      setKwError((e as Error).message);
    }
  };

  const onToggleKeyword = async (k: CollectionKeyword) => {
    try {
      await toggleArxivKeyword(k.id, !k.enabled);
      await loadKeywords();
    } catch (e) {
      setKwError((e as Error).message);
    }
  };

  // 검색 — override 가 주어지면 그것으로(키워드 클릭), 아니면 검색창 query 로.
  // 여러 키워드를 한꺼번에 검색하면 쿼리가 너무 길어 arxiv 가 거부하므로, 콤마는 최대
  // 5개까지만 허용(그 이상이면 잘라서 OR). 보통은 키워드 하나로 검색.
  const onSearch = async (override?: string) => {
    if (searching) return;
    const q = (override ?? query).trim();
    if (!q) return;
    if (override !== undefined) setQuery(q);
    setSearching(true);
    setSearchError(null);
    setSelected(new Set());
    try {
      const parts = q.split(",").map((s) => s.trim()).filter(Boolean);
      const r =
        parts.length > 1
          ? await searchArxiv({ keywords: parts.slice(0, 5), max_results: maxResults, sort_by: sortBy })
          : await searchArxiv({ query: q, max_results: maxResults, sort_by: sortBy });
      setPapers(r.papers);
      if (r.papers.length === 0) {
        setSearchError(ko ? "검색 결과가 없습니다." : "No results.");
      }
    } catch (e) {
      setSearchError((e as Error).message);
    } finally {
      setSearching(false);
    }
  };

  // 키워드 pill 클릭 → 그 키워드 하나로 검색
  const searchKeyword = (kw: string) => {
    void onSearch(kw);
  };

  const toggleSelect = (arxivId: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(arxivId)) next.delete(arxivId);
      else next.add(arxivId);
      return next;
    });
  };

  const onCollect = async () => {
    if (collecting || selected.size === 0) return;
    setCollecting(true);
    try {
      const r = await collectArxiv(Array.from(selected));
      setCollectLog(r.results);
      setSelected(new Set());
    } catch (e) {
      setCollectLog([
        { arxiv_id: "-", ok: false, item_id: null, created: null, title: null, error: (e as Error).message },
      ]);
    } finally {
      setCollecting(false);
    }
  };

  return (
    <main className="h-full overflow-y-auto p-6 max-w-5xl mx-auto w-full">
      <h1 className="text-xl font-semibold mb-1">{ko ? "🔭 arXiv 수집" : "🔭 arXiv Harvest"}</h1>
      <p className="text-xs text-zinc-500 mb-6">
        {ko
          ? "관심 키워드로 arXiv를 검색해 미리보고, 골라서 수집하면 PDF를 Docling으로 처리해 논문 위키가 자동 생성됩니다."
          : "Search arXiv by keywords, preview, and collect — selected PDFs are processed by Docling into paper wikis automatically."}
      </p>

      {/* ── 키워드 관리 ── */}
      <section className="mb-8 rounded-lg border border-zinc-200 dark:border-zinc-800 p-4">
        <h2 className="text-base font-semibold mb-2">{ko ? "관심 키워드" : "Collection Keywords"}</h2>
        {kwError && <div className="text-xs text-rose-600 mb-2">{kwError}</div>}
        <div className="flex gap-2 mb-3">
          <input
            type="text"
            value={newKw}
            onChange={(e) => setNewKw(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && onAddKeyword()}
            placeholder={ko ? "키워드 추가 (Enter)" : "Add keyword (Enter)"}
            className="flex-1 px-2 py-1.5 text-sm rounded border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900"
          />
          <button
            type="button"
            onClick={onAddKeyword}
            className="text-sm px-3 py-1.5 rounded bg-orange-500 text-white hover:bg-orange-600"
          >
            {ko ? "추가" : "Add"}
          </button>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {keywords.length === 0 && (
            <span className="text-xs text-zinc-400">
              {ko ? "아직 키워드가 없습니다." : "No keywords yet."}
            </span>
          )}
          {keywords.map((k) => (
            <span
              key={k.id}
              className={`inline-flex items-center gap-1 text-xs px-2 py-1 rounded border ${
                k.enabled
                  ? "border-orange-300 dark:border-orange-700 bg-orange-50 dark:bg-orange-900/20"
                  : "border-zinc-300 dark:border-zinc-700 text-zinc-400"
              }`}
            >
              <button
                type="button"
                onClick={() => onToggleKeyword(k)}
                title={ko ? "수집 대상 켜기/끄기" : "toggle enabled"}
                className="text-[10px]"
              >
                {k.enabled ? "●" : "○"}
              </button>
              <button
                type="button"
                onClick={() => searchKeyword(k.keyword)}
                title={ko ? "클릭해서 arXiv 검색" : "click to search arXiv"}
                className="hover:underline"
              >
                {k.keyword}
              </button>
              <button
                type="button"
                onClick={() => onDeleteKeyword(k.id)}
                className="text-zinc-400 hover:text-rose-500"
                title={ko ? "삭제" : "delete"}
              >
                ×
              </button>
            </span>
          ))}
        </div>
        {keywords.length > 0 && (
          <p className="mt-3 text-[11px] text-zinc-400">
            {ko
              ? "💡 키워드를 클릭하면 그 키워드로 arXiv를 검색합니다. (여러 키워드를 한꺼번에 모으는 전체 수집은 추후 자동 수집 기능에서 지원)"
              : "💡 Click a keyword to search arXiv with it. (Bulk collection across all keywords comes later via scheduled harvesting.)"}
          </p>
        )}
      </section>

      {/* ── 검색 미리보기 ── */}
      <section className="mb-8 rounded-lg border border-zinc-200 dark:border-zinc-800 p-4">
        <h2 className="text-base font-semibold mb-2">{ko ? "arXiv 검색" : "arXiv Search"}</h2>
        <div className="flex flex-wrap gap-2 mb-3">
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && onSearch()}
            placeholder={ko ? "쿼리 또는 키워드 (콤마로 여러 개)" : "query or keywords (comma-separated)"}
            className="flex-1 min-w-[240px] px-2 py-1.5 text-sm rounded border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900"
          />
          <select
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value)}
            className="text-sm px-2 py-1.5 rounded border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900"
          >
            <option value="relevance">{ko ? "관련도" : "Relevance"}</option>
            <option value="submittedDate">{ko ? "최신 제출" : "Newest"}</option>
            <option value="lastUpdatedDate">{ko ? "최근 수정" : "Updated"}</option>
          </select>
          <select
            value={maxResults}
            onChange={(e) => setMaxResults(Number(e.target.value))}
            className="text-sm px-2 py-1.5 rounded border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900"
          >
            {[10, 25, 50].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={() => onSearch()}
            disabled={searching}
            className="text-sm px-3 py-1.5 rounded bg-orange-500 text-white hover:bg-orange-600 disabled:opacity-50"
          >
            {searching ? (ko ? "검색 중…" : "Searching…") : ko ? "검색" : "Search"}
          </button>
        </div>
        {searchError && <div className="text-xs text-rose-600 mb-2">{searchError}</div>}

        {papers.length > 0 && (
          <>
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs text-zinc-500">
                {ko ? `${papers.length}건 · ${selected.size}건 선택` : `${papers.length} results · ${selected.size} selected`}
              </span>
              <button
                type="button"
                onClick={onCollect}
                disabled={collecting || selected.size === 0}
                className="text-sm px-3 py-1.5 rounded bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-40"
              >
                {collecting
                  ? ko ? "수집 중…" : "Collecting…"
                  : ko ? `선택 ${selected.size}건 수집` : `Collect ${selected.size}`}
              </button>
            </div>
            <ul className="space-y-1.5">
              {papers.map((p) => (
                <li
                  key={p.arxiv_id}
                  className="flex items-start gap-2 text-sm border border-zinc-200 dark:border-zinc-800 rounded p-2"
                >
                  <input
                    type="checkbox"
                    checked={selected.has(p.arxiv_id)}
                    onChange={() => toggleSelect(p.arxiv_id)}
                    className="mt-1"
                  />
                  <div className="min-w-0">
                    <a
                      href={p.abs_url}
                      target="_blank"
                      rel="noreferrer"
                      className="font-medium text-orange-700 dark:text-orange-400 hover:underline"
                    >
                      {p.title}
                    </a>
                    <div className="text-[11px] text-zinc-500 mt-0.5">
                      {p.arxiv_id}
                      {p.published && ` · ${p.published.slice(0, 10)}`}
                      {p.categories.length > 0 && ` · ${p.categories.slice(0, 3).join(", ")}`}
                    </div>
                    <div className="text-xs text-zinc-600 dark:text-zinc-400 line-clamp-2 mt-0.5">
                      {p.summary}
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          </>
        )}
      </section>

      {/* ── 수집 결과 로그 ── */}
      {collectLog.length > 0 && (
        <section className="mb-8 rounded-lg border border-zinc-200 dark:border-zinc-800 p-4">
          <h2 className="text-base font-semibold mb-2">{ko ? "수집 결과" : "Collection Result"}</h2>
          <ul className="space-y-1">
            {collectLog.map((r, i) => (
              <li
                key={i}
                className={`text-xs border-l-2 pl-2 ${r.ok ? "border-emerald-500" : "border-rose-500"}`}
              >
                <span className="font-mono">{r.arxiv_id}</span>{" "}
                {r.ok
                  ? `✓ ${r.created ? (ko ? "신규 수집" : "collected") : ko ? "이미 있음" : "duplicate"}${
                      r.title ? ` · ${r.title}` : ""
                    }`
                  : `✗ ${r.error}`}
              </li>
            ))}
          </ul>
          <p className="text-[11px] text-zinc-400 mt-2">
            {ko
              ? "수집된 자료는 백그라운드에서 분류·위키 합성됩니다. /wiki 에서 잠시 후 확인하세요."
              : "Collected items are classified and turned into wikis in the background. Check /wiki shortly."}
          </p>
        </section>
      )}
    </main>
  );
}
