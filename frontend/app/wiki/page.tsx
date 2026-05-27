"use client";

/**
 * D10 wave-1g — /wiki — wiki page list.
 *
 * 기능:
 *  - filter (status / q text)
 *  - pagination (50 per page)
 *  - 각 page card 클릭 → /wiki/[slug] 상세
 *  - body_status 칩 (empty/generating/ready/stale) 색상 구분
 *  - source_count 표시
 */

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import { listWikiPages, type WikiPageListItem } from "@/lib/api";

const PAGE_SIZE = 50;

const STATUS_COLORS: Record<string, string> = {
  ready:
    "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400",
  stale: "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400",
  generating:
    "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400 animate-pulse",
  empty: "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400",
};

export default function WikiListPage() {
  const router = useRouter();
  const searchParams = useSearchParams();

  // URL query 가 단일 진실 (2026-05-27) — detail 페이지에서 뒤로가기 시 검색/
  // 페이지 상태 보존. q/status/page/keyword 모두 URL query.
  // 의미 검색은 별 페이지 (/ask) — 여기는 단순 title/slug ILIKE 매칭 + filter 만.
  const q = searchParams.get("q") || "";
  const statusFilter = searchParams.get("status") || "";
  const keywordFilter = searchParams.get("keyword") || "";
  const page = Math.max(1, parseInt(searchParams.get("page") || "1", 10) || 1);

  // 입력창 local state (debounce 위함) — URL 의 q 와 분리, debounce 후 URL 반영
  const [qInput, setQInput] = useState(q);
  // URL 의 q 가 외부에서 바뀌면 (브라우저 history 등) input 도 sync
  useEffect(() => {
    setQInput(q);
  }, [q]);

  const [response, setResponse] = useState<{ total: number; pages: WikiPageListItem[] } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // URL 갱신 헬퍼 — 부분 변경 (다른 param 은 보존). replace 로 history 안 늘림.
  // page 만은 사용자가 의도해서 이동하므로 push 가 자연스럽지만, 단순화 위해 replace 통일.
  const updateQuery = (updates: Record<string, string | null>) => {
    const params = new URLSearchParams(searchParams.toString());
    for (const [k, v] of Object.entries(updates)) {
      if (v === null || v === "") params.delete(k);
      else params.set(k, v);
    }
    const qs = params.toString();
    router.replace(qs ? `/wiki?${qs}` : "/wiki", { scroll: false });
  };

  // q 입력 → 250ms debounce 후 URL 반영 (+ page=1 reset)
  useEffect(() => {
    if (qInput === q) return;     // 외부 sync 일 때 loop 방지
    const id = setTimeout(() => {
      updateQuery({ q: qInput || null, page: null });
    }, 250);
    return () => clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [qInput]);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    listWikiPages({
      status: statusFilter || undefined,
      q: q || undefined,
      keyword: keywordFilter || undefined,
      limit: PAGE_SIZE,
      offset: (page - 1) * PAGE_SIZE,
    })
      .then((r) => {
        if (alive) setResponse(r);
      })
      .catch((e) => {
        if (alive) setError((e as Error).message);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [q, statusFilter, keywordFilter, page]);

  const totalPages = response ? Math.max(1, Math.ceil(response.total / PAGE_SIZE)) : 1;

  return (
    <div className="flex-1 overflow-auto p-6 bg-zinc-50 dark:bg-zinc-950">
      <div className="max-w-5xl mx-auto">
        <header className="mb-6">
          <h1 className="text-2xl font-bold text-zinc-900 dark:text-zinc-100">
            📖 LinkMind Wiki
          </h1>
          <p className="text-sm text-zinc-600 dark:text-zinc-400 mt-1">
            자료들을 의미 단위 wiki 페이지로 자동 분류·합성. 페이지를 열면 LLM 이
            한국어 markdown 본문을 합성합니다 (vLLM Qwen2.5-7B).
          </p>
        </header>

        {/* Keyword filter chip */}
        {keywordFilter && (
          <div className="mb-4 inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-orange-100 dark:bg-orange-900/30 text-orange-700 dark:text-orange-300 text-xs">
            <span>🔖 keyword:</span>
            <span className="font-medium">{keywordFilter}</span>
            <button
              type="button"
              onClick={() => updateQuery({ keyword: null, page: null })}
              className="text-orange-500 hover:text-orange-700"
              title="필터 해제"
            >
              ×
            </button>
          </div>
        )}

        {/* Filters */}
        <div className="flex gap-2 mb-4 items-center">
          <input
            type="search"
            placeholder="제목 / slug 검색…"
            value={qInput}
            onChange={(e) => setQInput(e.target.value)}
            className="flex-1 px-3 py-1.5 text-sm rounded border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 text-zinc-900 dark:text-zinc-100"
          />
          <select
            value={statusFilter}
            onChange={(e) =>
              updateQuery({ status: e.target.value || null, page: null })
            }
            className="px-3 py-1.5 text-sm rounded border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 text-zinc-900 dark:text-zinc-100"
          >
            <option value="">전체 상태</option>
            <option value="ready">✅ ready</option>
            <option value="stale">🔄 stale</option>
            <option value="empty">⏳ empty</option>
            <option value="generating">⏱ generating</option>
          </select>
        </div>

        {/* List */}
        {error && (
          <div className="my-4 p-3 rounded bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-300 text-sm">
            에러: {error}
          </div>
        )}

        {loading && !response ? (
          <div className="text-center py-10 text-zinc-500">로딩 중…</div>
        ) : (
          <>
            <div className="text-xs text-zinc-500 dark:text-zinc-400 mb-2">
              {response?.total ?? 0} 페이지 (페이지 {page} / {totalPages})
            </div>

            <ul className="space-y-2">
              {response?.pages.map((p) => (
                <li key={p.id}>
                  <Link
                    href={`/wiki/${encodeURIComponent(p.slug)}`}
                    className="block p-3 rounded border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 hover:border-orange-400 dark:hover:border-orange-500 transition"
                  >
                    <div className="flex items-center gap-2">
                      {p.is_pinned && <span className="text-xs">📌</span>}
                      <h3 className="font-medium text-zinc-900 dark:text-zinc-100 truncate flex-1">
                        {p.title}
                      </h3>
                      <span
                        className={`text-[10px] px-1.5 py-0.5 rounded ${STATUS_COLORS[p.body_status] || STATUS_COLORS.empty}`}
                      >
                        {p.body_status}
                      </span>
                      <span className="text-[10px] text-zinc-500 dark:text-zinc-400">
                        {p.source_count} sources
                      </span>
                    </div>
                    {p.description && (
                      <p className="mt-1 text-xs text-zinc-600 dark:text-zinc-400 line-clamp-2">
                        {p.description}
                      </p>
                    )}
                    <p className="mt-1 text-[10px] text-zinc-400 dark:text-zinc-500 font-mono truncate">
                      {p.slug}
                    </p>
                  </Link>
                </li>
              ))}
            </ul>

            {/* Pagination */}
            {totalPages > 1 && (
              <div className="flex items-center justify-center gap-2 mt-6">
                <button
                  type="button"
                  onClick={() => {
                    const next = Math.max(1, page - 1);
                    updateQuery({ page: next > 1 ? String(next) : null });
                  }}
                  disabled={page <= 1}
                  className="px-3 py-1 text-xs rounded border border-zinc-300 dark:border-zinc-700 disabled:opacity-40"
                >
                  ← 이전
                </button>
                <span className="text-xs text-zinc-500">
                  {page} / {totalPages}
                </span>
                <button
                  type="button"
                  onClick={() => {
                    const next = Math.min(totalPages, page + 1);
                    updateQuery({ page: String(next) });
                  }}
                  disabled={page >= totalPages}
                  className="px-3 py-1 text-xs rounded border border-zinc-300 dark:border-zinc-700 disabled:opacity-40"
                >
                  다음 →
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
