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

import {
  batchRegenerateWiki,
  getWikiStats,
  listWikiPages,
  type WikiPageListItem,
  type WikiStatsResponse,
} from "@/lib/api";

const PAGE_SIZE_OPTIONS = [10, 50, 100] as const;
const DEFAULT_PAGE_SIZE = 50;

const STATUS_COLORS: Record<string, string> = {
  ready:
    "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400",
  stale: "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400",
  generating:
    "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400 animate-pulse",
  empty: "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400",
};

// backend body_status → 사용자 친화 라벨 (2026-05-27).
// 'ready' → 'completed' (처리 완료) / 'empty' → 'ready' (사용자 클릭 시 lazy 합성).
// stale + generating 은 그대로 표시 (개별 card 의 chip — 디버깅 정보 유지).
const STATUS_LABEL: Record<string, string> = {
  ready: "completed",
  empty: "ready",
  stale: "stale",
  generating: "generating",
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

  // page_size — URL ?page_size=10/50/100 (default 50). 사용자 라디오 선택.
  const rawPageSize = parseInt(
    searchParams.get("page_size") || String(DEFAULT_PAGE_SIZE), 10,
  );
  const pageSize: number = (PAGE_SIZE_OPTIONS as readonly number[]).includes(rawPageSize)
    ? rawPageSize
    : DEFAULT_PAGE_SIZE;

  // 입력창 local state (debounce 위함) — URL 의 q 와 분리, debounce 후 URL 반영
  const [qInput, setQInput] = useState(q);
  // URL 의 q 가 외부에서 바뀌면 (브라우저 history 등) input 도 sync
  useEffect(() => {
    setQInput(q);
  }, [q]);

  const [response, setResponse] = useState<{ total: number; pages: WikiPageListItem[] } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 상태별 count — tab UI + 일괄 합성 버튼 라벨 (2026-05-27)
  const [stats, setStats] = useState<WikiStatsResponse | null>(null);

  // 일괄 합성 confirm + 진행 상태
  const [confirmBatch, setConfirmBatch] = useState(false);
  const [batchInProgress, setBatchInProgress] = useState(false);
  const [batchMessage, setBatchMessage] = useState<string | null>(null);

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
      limit: pageSize,
      offset: (page - 1) * pageSize,
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
  }, [q, statusFilter, keywordFilter, page, pageSize]);

  // stats fetch — mount 시 1회 + 일괄 합성 진행 중 5초 polling.
  const refreshStats = () => {
    getWikiStats()
      .then((s) => setStats(s))
      .catch((e) => setError((e as Error).message));
  };
  useEffect(() => {
    refreshStats();
  }, []);

  // 일괄 합성 진행 중 polling — stats.generating 이 0 되면 종료.
  useEffect(() => {
    if (!batchInProgress) return;
    const id = setInterval(() => {
      getWikiStats()
        .then((s) => {
          setStats(s);
          if (s.generating === 0) {
            setBatchInProgress(false);
            setBatchMessage(`✓ 일괄 합성 완료. ready ${s.ready}건`);
            // list 도 같이 refresh
            setQInput((v) => v);   // useEffect trigger 위한 no-op
          }
        })
        .catch(() => {});
    }, 5000);
    return () => clearInterval(id);
  }, [batchInProgress]);

  const totalPages = response ? Math.max(1, Math.ceil(response.total / pageSize)) : 1;

  const onBatchRegenerate = async () => {
    setConfirmBatch(false);
    setBatchInProgress(true);
    setBatchMessage(null);
    setError(null);
    try {
      // statusFilter 에 따라 stale (pending tab) 또는 empty (empty tab) 처리.
      // backend 'pending' alias 가 stale 로 매핑됨.
      const batchStatus =
        statusFilter === "pending" ? "stale" : "empty";
      const r = await batchRegenerateWiki({ status: batchStatus, limit: 10 });
      setBatchMessage(
        `일괄 합성 시작 — ${r.dispatched}건 dispatch · 예상 ${r.estimated_seconds}초`,
      );
      refreshStats();
    } catch (e) {
      setError((e as Error).message);
      setBatchInProgress(false);
    }
  };

  // status 별 count chip (2026-05-27 단순화 + 사용자 친화 라벨):
  //   - stale + generating → 'pending' (둘 다 "처리 대기/진행 중")
  //   - backend body_status='ready' → 라벨 'completed' (처리 완료, 의미 명확)
  //   - backend body_status='empty' → 라벨 'ready' (사용자가 클릭하면 lazy 합성됨)
  //   backend column 값은 그대로 (의미 정확, schema 변경 회피). frontend label
  //   만 mapping — URL query key (status=ready/empty) 도 backend 값 그대로.
  type StatusKey = "" | "ready" | "pending" | "empty";
  const STATUS_TABS: { key: StatusKey; label: string; icon: string }[] = [
    { key: "", label: "전체", icon: "" },
    { key: "ready", label: "completed", icon: "✅" },
    { key: "pending", label: "pending", icon: "⏱" },
    { key: "empty", label: "ready", icon: "⏳" },
  ];
  const countFor = (k: StatusKey): number | null => {
    if (!stats) return null;
    if (k === "") return stats.total;
    if (k === "pending") return stats.stale + stats.generating;
    return (stats as unknown as Record<string, number>)[k] ?? 0;
  };

  // pending 예상 처리 시간 (2026-05-27 통일):
  //   daemon 과 batch 모두 concurrency 4 (~1s/page effective, vLLM continuous
  //   batching). 사용자 요청 — 둘 분리 의미 X, 같은 로직으로 통일.
  const _fmtSec = (sec: number): string => {
    if (sec < 60) return `${sec}초`;
    if (sec < 3600) return `${Math.round(sec / 60)}분`;
    if (sec < 86400) return `${Math.round(sec / 3600)}시간`;
    const d = Math.floor(sec / 86400);
    const h = Math.round((sec % 86400) / 3600);
    return h > 0 ? `${d}일 ${h}시간` : `${d}일`;
  };
  const formatEta = (pendingCount: number): string => {
    if (pendingCount <= 0) return "";
    return `~${_fmtSec(pendingCount)}`;  // concurrency 4 effective ~1s/page
  };

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

        {/* Status tabs — 각 status 의 count 시각화 (2026-05-27) */}
        <div className="flex flex-wrap gap-1.5 mb-3">
          {STATUS_TABS.map((tab) => {
            const active = statusFilter === tab.key;
            const count = countFor(tab.key);
            return (
              <button
                key={tab.key || "all"}
                type="button"
                onClick={() =>
                  updateQuery({ status: tab.key || null, page: null })
                }
                className={`px-2.5 py-1 text-xs rounded border transition ${
                  active
                    ? "border-orange-400 dark:border-orange-500 bg-orange-50 dark:bg-orange-900/30 text-orange-700 dark:text-orange-300 font-medium"
                    : "border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 text-zinc-700 dark:text-zinc-300 hover:border-zinc-400"
                }`}
              >
                <span className="mr-1">{tab.icon}</span>
                {tab.label}
                {count !== null && (
                  <span
                    className={`ml-1.5 text-[10px] ${
                      count === 0
                        ? "text-zinc-400"
                        : active
                          ? "text-orange-600 dark:text-orange-400"
                          : "text-zinc-500"
                    }`}
                  >
                    ({count.toLocaleString()})
                  </span>
                )}
                {/* pending tab ETA — daemon = batch 통일 (concurrency 4 effective ~1s/page) */}
                {tab.key === "pending" && count !== null && count > 0 && (
                  <span
                    className="ml-1.5 text-[10px] text-blue-600 dark:text-blue-400"
                    title="자동 처리 예상 시간 (concurrency 4, ~1초/page effective — vLLM continuous batching)"
                  >
                    {formatEta(count)}
                  </span>
                )}
              </button>
            );
          })}
        </div>

        {/* Search input + status=empty 일 때 일괄 합성 버튼 */}
        <div className="flex gap-2 mb-4 items-center">
          <input
            type="search"
            placeholder="제목 / slug 검색…"
            value={qInput}
            onChange={(e) => setQInput(e.target.value)}
            className="flex-1 px-3 py-1.5 text-sm rounded border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 text-zinc-900 dark:text-zinc-100"
          />
          {/* 일괄 합성 버튼 — pending/empty tab 둘 다 노출 (2026-05-27) */}
          {((statusFilter === "pending" && stats && stats.stale > 0) ||
            (statusFilter === "empty" && stats && stats.empty > 0)) && (
            <button
              type="button"
              onClick={() => setConfirmBatch(true)}
              disabled={batchInProgress}
              className="px-3 py-1.5 text-xs rounded border border-blue-400 dark:border-blue-600 bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 hover:bg-blue-100 dark:hover:bg-blue-900/40 disabled:opacity-50"
            >
              {batchInProgress
                ? "⏱ 합성 중…"
                : `⚡ ${
                    statusFilter === "pending"
                      ? stats!.stale
                      : stats!.empty
                  }건 일괄 합성 (최대 10건)`}
            </button>
          )}
        </div>

        {/* 일괄 합성 confirm 박스 */}
        {confirmBatch && (
          <div className="mb-3 p-3 rounded bg-blue-50 dark:bg-blue-900/20 border border-blue-300 dark:border-blue-800 text-xs">
            <div className="font-medium text-blue-800 dark:text-blue-200 mb-1">
              empty wiki 일괄 합성?
            </div>
            <div className="text-zinc-700 dark:text-zinc-300 mb-2">
              • 최대 10건의 empty wiki 를 자동 합성 (concurrency 4 — vLLM 부하 분산)<br />
              • 예상 시간: ~40초 (page 당 ~4초)<br />
              • 진행 상태는 generating 탭의 count 로 polling 됩니다
            </div>
            <div className="flex gap-1.5">
              <button
                type="button"
                onClick={() => setConfirmBatch(false)}
                className="flex-1 px-3 py-1 text-xs bg-white dark:bg-zinc-900 border border-zinc-300 dark:border-zinc-700 rounded hover:bg-zinc-100"
              >
                취소
              </button>
              <button
                type="button"
                onClick={onBatchRegenerate}
                className="flex-1 px-3 py-1 text-xs bg-blue-600 hover:bg-blue-700 text-white rounded font-medium"
              >
                일괄 합성 시작
              </button>
            </div>
          </div>
        )}

        {batchMessage && (
          <div className="mb-3 p-2 rounded bg-emerald-50 dark:bg-emerald-900/20 border border-emerald-300 dark:border-emerald-800 text-xs text-emerald-800 dark:text-emerald-200">
            {batchMessage}
          </div>
        )}

        {/* List */}
        {error && (
          <div className="my-4 p-3 rounded bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-300 text-sm">
            에러: {error}
          </div>
        )}

        {loading && !response ? (
          <div className="text-center py-10 text-zinc-500">로딩 중…</div>
        ) : (() => {
          // pagination 버튼 — list 위/아래 양쪽에 동일 표시 (2026-05-27, 사용자 요청).
          const pagerBar = (response?.total ?? 0) > 0 ? (
            <div className="flex items-center justify-center gap-2">
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
          ) : null;

          return (
          <>
            {/* 결과 카운트 + 페이지당 개수 라디오 */}
            <div className="flex items-center justify-between mb-2 text-xs text-zinc-500 dark:text-zinc-400">
              <div>
                총 <span className="font-medium text-zinc-700 dark:text-zinc-300">
                  {response?.total ?? 0}
                </span>건 · 페이지{" "}
                <span className="font-medium text-zinc-700 dark:text-zinc-300">
                  {page}
                </span>{" "}
                / {totalPages}
              </div>
              {/* 페이지당 개수 — 10/50/100 라디오 (2026-05-27) */}
              <div className="flex items-center gap-1.5">
                <span>페이지당</span>
                {PAGE_SIZE_OPTIONS.map((n) => {
                  const active = pageSize === n;
                  return (
                    <button
                      key={n}
                      type="button"
                      onClick={() =>
                        updateQuery({
                          page_size: n === DEFAULT_PAGE_SIZE ? null : String(n),
                          page: null,
                        })
                      }
                      className={`px-2 py-0.5 rounded text-[11px] border ${
                        active
                          ? "border-orange-400 dark:border-orange-500 bg-orange-50 dark:bg-orange-900/30 text-orange-700 dark:text-orange-300 font-medium"
                          : "border-zinc-300 dark:border-zinc-700 hover:border-zinc-400"
                      }`}
                    >
                      {n}
                    </button>
                  );
                })}
              </div>
            </div>

            {/* Pagination 상단 (list 위) */}
            {pagerBar && <div className="mb-3">{pagerBar}</div>}

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
                        {STATUS_LABEL[p.body_status] || p.body_status}
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

            {/* Pagination 하단 (list 아래) — 위와 동일 */}
            {pagerBar && <div className="mt-6">{pagerBar}</div>}
          </>
          );
        })()}
      </div>
    </div>
  );
}
