"use client";

/**
 * D10 wave-1g — /wiki — wiki page list.
 *
 * 기능:
 *  - filter (status tab / q text / 다중 키워드 AND)
 *  - 상단 키워드 cloud (2026-05-29): 빈도순 상위 키워드 클릭 토글 선택 (접기/펼치기)
 *  - 정렬 select (2026-05-29): 날짜순 최신/오래된 · 가나다 오름/내림 (URL ?sort=)
 *  - pagination (페이지당 10/50/100)
 *  - 각 page card 클릭 → /wiki/[slug] 상세 (completed 만)
 *  - body_status 칩 (issues/pending(generating·queuing)/completed) 색상 구분
 *  - keywords pill (2026-05-29) — 클릭 시 ?keyword= 필터. source_count 표시
 */

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import {
  batchRegenerateWiki,
  getWikiStats,
  listWikiPages,
  searchWikiKeywords,
  type WikiKeywordSuggestion,
  type WikiPageListItem,
  type WikiSort,
  type WikiStatsResponse,
} from "@/lib/api";

const PAGE_SIZE_OPTIONS = [10, 50, 100] as const;
const DEFAULT_PAGE_SIZE = 50;

// 상단 키워드 cloud — distinct 키워드 90k+ 라 literally 전부는 브라우저 한계상
// 불가 (버튼 9만 개 = freeze). 빈도순 상위를 넉넉히 (500) fetch + 펼치면 스크롤.
const TOP_KEYWORDS_LIMIT = 500;    // fetch 개수 (빈도순 상위)
const COLLAPSED_KEYWORDS = 30;     // 접힌 상태 표시 개수

// 정렬 옵션 (2026-05-29) — 날짜는 합성 시각(body_generated_at) 우선. URL ?sort= 동기화.
const SORT_OPTIONS: { key: WikiSort; label: string }[] = [
  { key: "recent", label: "날짜순 (최신)" },
  { key: "oldest", label: "날짜순 (오래된)" },
  { key: "alpha", label: "가나다 (오름)" },
  { key: "alpha_desc", label: "가나다 (내림)" },
];
const SORT_KEYS = SORT_OPTIONS.map((o) => o.key);
const DEFAULT_SORT: WikiSort = "recent";

// 2026-05-27 통일: backend body_status = frontend label.
//   - 'ready'     — ingest 직후, body 미합성 (사용자 클릭 시 lazy)
//   - 'pending'   — 처리 대기/진행 (옛 stale + generating 통합). pending 안에서
//                    sub-state 두 종 — 'generating' (LLM 합성 중) / 'queuing' (대기열).
//                    started_at NOT NULL + 5분 안 → generating, 그 외 → queuing.
//   - 'completed' — 처리 완료 (body 있음)
const STATUS_COLORS: Record<string, string> = {
  completed:
    "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400",
  generating:
    "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400 animate-pulse",
  queuing:
    "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400",
  // 'issues' (옛 'ready') — 처리 못 끝낸 잔여 자료 (실패 reset / stuck)
  issues: "bg-rose-100 text-rose-700 dark:bg-rose-900/30 dark:text-rose-400",
};

const PROCESSING_TIMEOUT_MS = 5 * 60 * 1000;   // 5분 후엔 stuck 으로 간주

function effectiveStatusKey(p: WikiPageListItem): string {
  if (p.body_status !== "pending") return p.body_status;
  if (!p.body_processing_started_at) return "queuing";
  const startedMs = new Date(p.body_processing_started_at).getTime();
  if (Number.isNaN(startedMs)) return "queuing";
  if (Date.now() - startedMs > PROCESSING_TIMEOUT_MS) return "queuing";
  return "generating";
}

// MM-DD HH:MM (local time) — 어떤 wiki 가 backfill / classifier batch / 신규 ingest
// 어디에서 왔는지 사용자 시각 구분.
function fmtDateShort(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mi = String(d.getMinutes()).padStart(2, "0");
  return `${mm}-${dd} ${hh}:${mi}`;
}

export default function WikiListPage() {
  const router = useRouter();
  const searchParams = useSearchParams();

  // URL query 가 단일 진실 (2026-05-27) — detail 페이지에서 뒤로가기 시 검색/
  // 페이지 상태 보존. q/status/page/keyword 모두 URL query.
  // 의미 검색은 별 페이지 (/ask) — 여기는 단순 title/slug ILIKE 매칭 + filter 만.
  const q = searchParams.get("q") || "";
  const statusFilter = searchParams.get("status") || "";
  // 다중 키워드 (AND) — ?keyword=A&keyword=B (getAll). keywordKey 는 effect dep 용
  // 안정 문자열 (배열은 매 렌더 새 참조라 dep 로 못 씀).
  const keywordFilters = searchParams.getAll("keyword");
  const keywordKey = JSON.stringify(keywordFilters);
  const page = Math.max(1, parseInt(searchParams.get("page") || "1", 10) || 1);

  // page_size — URL ?page_size=10/50/100 (default 50). 사용자 라디오 선택.
  const rawPageSize = parseInt(
    searchParams.get("page_size") || String(DEFAULT_PAGE_SIZE), 10,
  );
  const pageSize: number = (PAGE_SIZE_OPTIONS as readonly number[]).includes(rawPageSize)
    ? rawPageSize
    : DEFAULT_PAGE_SIZE;

  // 정렬 — URL ?sort= (default recent). allowlist 밖이면 default 로 fallback.
  const rawSort = searchParams.get("sort") || DEFAULT_SORT;
  const sort: WikiSort = (SORT_KEYS as string[]).includes(rawSort)
    ? (rawSort as WikiSort)
    : DEFAULT_SORT;

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

  // 상단 키워드 cloud (2026-05-29) — 빈도순 상위. mount 시 1회 fetch + 접기/펼치기.
  const [topKeywords, setTopKeywords] = useState<WikiKeywordSuggestion[]>([]);
  const [keywordsExpanded, setKeywordsExpanded] = useState(false);
  useEffect(() => {
    searchWikiKeywords("", TOP_KEYWORDS_LIMIT)
      .then((r) => setTopKeywords(r.suggestions))
      .catch(() => {});
  }, []);

  // 키워드 cloud 내 검색 (2026-05-29) — distinct 50k+ 라 cloud 는 빈도순 상위만.
  // 빈도 낮은 키워드(예: point-cloud-compression)도 검색으로 찾아 선택 가능.
  const [kwQuery, setKwQuery] = useState("");
  const [kwResults, setKwResults] = useState<WikiKeywordSuggestion[]>([]);
  useEffect(() => {
    const term = kwQuery.trim();
    if (!term) {
      setKwResults([]);
      return;
    }
    let alive = true;
    const id = setTimeout(() => {
      searchWikiKeywords(term, 120)
        .then((r) => {
          if (alive) setKwResults(r.suggestions);
        })
        .catch(() => {});
    }, 250);
    return () => {
      alive = false;
      clearTimeout(id);
    };
  }, [kwQuery]);

  // 일괄 합성 — ready (처리 실패 reset 또는 default INSERT 자료) 강제 재처리용.
  // 2026-05-27 사용자 명시: pending 은 daemon 이 자동 처리하지만 ready 는 fetch
  // SQL 도 매칭하므로 보통 자동 처리됨. 다만 daemon 가 idle 일 때 또는 사용자가
  // 급히 일괄 처리 원할 때 안전망 역할.
  const [confirmBatch, setConfirmBatch] = useState(false);
  const [batchInProgress, setBatchInProgress] = useState(false);
  const [batchMessage, setBatchMessage] = useState<string | null>(null);

  const onBatchRegenerate = async () => {
    setConfirmBatch(false);
    setBatchInProgress(true);
    setBatchMessage(null);
    setError(null);
    try {
      const r = await batchRegenerateWiki({ status: "issues", limit: 10 });
      setBatchMessage(
        `일괄 합성 dispatched — ${r.dispatched}건 · 예상 ~${r.estimated_seconds}초`,
      );
      // stats refresh 후 곧 dispatched count 가 pending 으로 이동
      setTimeout(() => {
        getWikiStats().then(setStats).catch(() => {});
        setBatchInProgress(false);
      }, 1500);
    } catch (e) {
      setError((e as Error).message);
      setBatchInProgress(false);
    }
  };

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

  // 다중 키워드 — keyword 는 multi-value 라 updateQuery (set) 로 못 다룸. 전체 교체.
  const setKeywords = (next: string[]) => {
    const params = new URLSearchParams(searchParams.toString());
    params.delete("keyword");
    for (const k of next) params.append("keyword", k);
    params.delete("page");      // 필터 바뀌면 1페이지로
    const qs = params.toString();
    router.replace(qs ? `/wiki?${qs}` : "/wiki", { scroll: false });
  };
  // 키워드 toggle — 이미 있으면 제거, 없으면 추가 (AND 누적 검색).
  const toggleKeyword = (kw: string) => {
    setKeywords(
      keywordFilters.includes(kw)
        ? keywordFilters.filter((k) => k !== kw)
        : [...keywordFilters, kw],
    );
  };

  // 키워드 cloud/검색 결과 chip 렌더 — 선택 시 파란색 강조 (2026-05-29, 적색 →
  // 파랑: 키워드는 정보성 태그라 차분하게). 클릭 = 토글 (다중 AND).
  const renderKwChip = (s: WikiKeywordSuggestion) => {
    const selected = keywordFilters.includes(s.keyword);
    return (
      <button
        key={s.keyword}
        type="button"
        onClick={() => toggleKeyword(s.keyword)}
        title={
          selected
            ? `'${s.keyword}' 필터 해제`
            : `'${s.keyword}' 추가 (${s.usage_count}개 wiki)`
        }
        className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] border transition ${
          selected
            ? "border-blue-500 bg-blue-500 text-white dark:bg-blue-600 dark:border-blue-500 font-medium"
            : "border-zinc-300 dark:border-zinc-700 bg-zinc-50 dark:bg-zinc-800/60 text-zinc-700 dark:text-zinc-300 hover:border-blue-400 hover:text-blue-600 dark:hover:text-blue-400"
        }`}
      >
        {selected && <span>✓</span>}
        <span>{s.keyword}</span>
        <span className={selected ? "opacity-80" : "text-zinc-400 dark:text-zinc-500"}>
          {s.usage_count}
        </span>
      </button>
    );
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
      keyword: keywordFilters,
      sort,
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
    // keywordKey = JSON.stringify(keywordFilters) — 배열 dep 대신 안정 문자열
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, statusFilter, keywordKey, sort, page, pageSize]);

  // stats fetch — mount 시 1회 + pending 이 있으면 10초 polling (자동 daemon
  // 진행 상황 시각화). pending=0 이면 polling 종료.
  const refreshStats = () => {
    getWikiStats()
      .then((s) => setStats(s))
      .catch((e) => setError((e as Error).message));
  };
  useEffect(() => {
    refreshStats();
  }, []);
  useEffect(() => {
    if (!stats || stats.pending === 0) return;
    const id = setInterval(refreshStats, 10000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stats?.pending]);

  const totalPages = response ? Math.max(1, Math.ceil(response.total / pageSize)) : 1;

  // 2026-05-27 통일: tab key = backend body_status value (헷갈림 X).
  type StatusKey = "" | "issues" | "pending" | "completed";
  const STATUS_TABS: { key: StatusKey; label: string; icon: string }[] = [
    { key: "", label: "전체", icon: "" },
    { key: "completed", label: "completed", icon: "✅" },
    { key: "issues", label: "issues", icon: "⏳" },
    { key: "pending", label: "pending", icon: "⏱" },
  ];
  const countFor = (k: StatusKey): number | null => {
    if (!stats) return null;
    if (k === "") return stats.total;
    return (stats as unknown as Record<string, number>)[k] ?? 0;
  };

  // pending 예상 처리 시간 (2026-05-27 통일):
  //   daemon 과 batch 모두 concurrency 4 (~1s/page effective, vLLM continuous
  //   batching). 사용자 요청 — 둘 분리 의미 X, 같은 로직으로 통일.
  const _fmtSec = (sec: number): string => {
    if (sec < 60) return `${sec}초`;
    if (sec < 3600) return `${Math.round(sec / 60)}분`;
    // 1시간~24시간: 시간 + 분 (분은 반올림, 0 분이면 생략)
    if (sec < 86400) {
      const h = Math.floor(sec / 3600);
      const m = Math.round((sec % 3600) / 60);
      // 분 반올림이 60 도달 시 시간 +1 + 분 0
      if (m === 60) return `${h + 1}시간`;
      return m > 0 ? `${h}시간 ${m}분` : `${h}시간`;
    }
    const d = Math.floor(sec / 86400);
    const h = Math.round((sec % 86400) / 3600);
    if (h === 24) return `${d + 1}일`;
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

        {/* 키워드 cloud (2026-05-29) — 빈도순 상위 + 검색. 클릭하면 토글 선택
            (다중 = AND). distinct 50k+ 라 cloud 는 상위만 + 검색으로 전부 접근. */}
        {(topKeywords.length > 0 || kwQuery) && (
          <div className="mb-4 p-3 rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900">
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-medium text-zinc-600 dark:text-zinc-400">
                🔖 키워드로 찾기{" "}
                <span className="font-normal text-zinc-400">
                  (클릭해서 다중 선택 · AND)
                </span>
              </span>
              {!kwQuery && topKeywords.length > COLLAPSED_KEYWORDS && (
                <button
                  type="button"
                  onClick={() => setKeywordsExpanded((v) => !v)}
                  className="text-[11px] text-blue-600 dark:text-blue-400 hover:underline whitespace-nowrap ml-2"
                >
                  {keywordsExpanded
                    ? "접기 ▴"
                    : `더 보기 (+${topKeywords.length - COLLAPSED_KEYWORDS}) ▾`}
                </button>
              )}
            </div>
            {/* 검색 — 빈도 낮은 키워드도 찾기 (cloud 는 상위만 노출하므로) */}
            <input
              type="search"
              value={kwQuery}
              onChange={(e) => setKwQuery(e.target.value)}
              placeholder="키워드 검색 (빈도 낮은 것도 — 예: point-cloud-compression)…"
              className="w-full mb-2 px-2.5 py-1 text-xs rounded border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 text-zinc-900 dark:text-zinc-100"
            />
            {kwQuery.trim() ? (
              <div className="flex flex-wrap gap-1.5 max-h-[45vh] overflow-y-auto pr-1">
                {kwResults.length > 0 ? (
                  kwResults.map(renderKwChip)
                ) : (
                  <span className="text-xs text-zinc-400 py-1">
                    '{kwQuery.trim()}' 매칭 키워드 없음
                  </span>
                )}
              </div>
            ) : (
              <div
                className={`flex flex-wrap gap-1.5 ${
                  keywordsExpanded ? "max-h-[45vh] overflow-y-auto pr-1" : ""
                }`}
              >
                {(keywordsExpanded
                  ? topKeywords
                  : topKeywords.slice(0, COLLAPSED_KEYWORDS)
                ).map(renderKwChip)}
              </div>
            )}
          </div>
        )}

        {/* 다중 키워드 필터 chip (AND) — 선택한 키워드 각각 제거 + 전체 해제 */}
        {keywordFilters.length > 0 && (
          <div className="mb-4 flex flex-wrap items-center gap-1.5 text-xs">
            <span className="text-zinc-500 dark:text-zinc-400">
              🔖 키워드 {keywordFilters.length > 1 ? `(모두 포함 · AND)` : ""}:
            </span>
            {keywordFilters.map((kw) => (
              <span
                key={kw}
                className="inline-flex items-center gap-1 px-2 py-1 rounded-full bg-orange-100 dark:bg-orange-900/30 text-orange-700 dark:text-orange-300"
              >
                <span className="font-medium">{kw}</span>
                <button
                  type="button"
                  onClick={() => toggleKeyword(kw)}
                  className="text-orange-500 hover:text-orange-700"
                  title={`'${kw}' 제거`}
                >
                  ×
                </button>
              </span>
            ))}
            {keywordFilters.length > 1 && (
              <button
                type="button"
                onClick={() => setKeywords([])}
                className="px-2 py-1 rounded-full border border-zinc-300 dark:border-zinc-700 text-zinc-500 hover:text-zinc-700 hover:border-zinc-400"
                title="키워드 필터 전체 해제"
              >
                전체 해제
              </button>
            )}
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
          {/* 일괄 합성 버튼 — issues tab 일 때 항상 노출. 0건이면 disabled. */}
          {statusFilter === "issues" && (
            <button
              type="button"
              onClick={() => setConfirmBatch(true)}
              disabled={batchInProgress || !stats || stats.issues === 0}
              title={
                stats && stats.issues === 0
                  ? "처리할 issues 자료 없음"
                  : "재시도 트리거 — 최대 10건 dispatch"
              }
              className="px-3 py-1.5 text-xs rounded border border-blue-400 dark:border-blue-600 bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 hover:bg-blue-100 dark:hover:bg-blue-900/40 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {batchInProgress
                ? "⏱ 합성 중…"
                : `⚡ 일괄 합성 (동시 최대 10건)`}
            </button>
          )}
        </div>

        {/* 일괄 합성 confirm */}
        {confirmBatch && (
          <div className="mb-3 p-3 rounded bg-blue-50 dark:bg-blue-900/20 border border-blue-300 dark:border-blue-800 text-xs">
            <div className="font-medium text-blue-800 dark:text-blue-200 mb-1">
              ready wiki 일괄 합성?
            </div>
            <div className="text-zinc-700 dark:text-zinc-300 mb-2">
              • 최대 10건 dispatch (concurrency 4) — ~10초 안에 시작<br />
              • 처리는 daemon 과 동시 — 빠른 progress<br />
              • dispatched 자료는 pending 으로 즉시 이동
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
                시작
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
              {/* 정렬 select (2026-05-29) + 페이지당 개수 라디오 */}
              <div className="flex items-center gap-3">
                <div className="flex items-center gap-1.5">
                  <span>정렬</span>
                  <select
                    value={sort}
                    onChange={(e) =>
                      updateQuery({
                        sort: e.target.value === DEFAULT_SORT ? null : e.target.value,
                        page: null,
                      })
                    }
                    className="px-1.5 py-0.5 rounded text-[11px] border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 text-zinc-700 dark:text-zinc-300"
                  >
                    {SORT_OPTIONS.map((o) => (
                      <option key={o.key} value={o.key}>
                        {o.label}
                      </option>
                    ))}
                  </select>
                </div>
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
            </div>

            {/* Pagination 상단 (list 위) */}
            {pagerBar && <div className="mb-3">{pagerBar}</div>}

            <ul className="space-y-2">
              {response?.pages.map((p) => {
                const ek = effectiveStatusKey(p);
                // 2026-05-27: completed 만 클릭 가능. pending / issues 는 disabled
                // — 사용자 명시: 처리 중 중복 처리 방지. lazy 합성도 제거.
                const isClickable = p.body_status === "completed";

                const inner = (
                  <>
                    <div className="flex items-center gap-2">
                      {p.is_pinned && <span className="text-xs">📌</span>}
                      <h3 className="font-medium text-zinc-900 dark:text-zinc-100 truncate flex-1">
                        {p.title}
                      </h3>
                      <span
                        className={`text-[10px] px-1.5 py-0.5 rounded ${STATUS_COLORS[ek] || STATUS_COLORS.issues}`}
                      >
                        {ek}
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
                    <div className="mt-1 flex items-center gap-2 text-[10px] text-zinc-400 dark:text-zinc-500">
                      <span className="font-mono truncate flex-1">{p.slug}</span>
                      <span
                        className="whitespace-nowrap"
                        title={`생성: ${p.created_at ?? "—"}\n갱신: ${p.updated_at ?? "—"}`}
                      >
                        📅 {fmtDateShort(p.created_at)}
                        {p.updated_at && p.updated_at !== p.created_at && (
                          <span className="ml-1 text-zinc-300 dark:text-zinc-600">
                            · ↻ {fmtDateShort(p.updated_at)}
                          </span>
                        )}
                      </span>
                    </div>
                    {/* keywords pills (2026-05-29) — 전체 표시 + 클릭 토글 (AND 누적).
                        선택된 키워드는 강조. 카드가 Link 라 prevent/stopPropagation 으로
                        네비 막고 필터만. */}
                    {p.keywords && p.keywords.length > 0 && (
                      <div className="mt-1.5 flex flex-wrap gap-1">
                        {p.keywords.map((kw) => {
                          const selected = keywordFilters.includes(kw);
                          return (
                            <button
                              key={kw}
                              type="button"
                              onClick={(e) => {
                                e.preventDefault();
                                e.stopPropagation();
                                toggleKeyword(kw);
                              }}
                              title={
                                selected
                                  ? `'${kw}' 필터 해제`
                                  : `'${kw}' 추가 (다중 = 모두 포함)`
                              }
                              className={`px-1.5 py-0.5 rounded text-[10px] border transition ${
                                selected
                                  ? "border-orange-500 bg-orange-500 text-white dark:bg-orange-600 dark:border-orange-500 font-medium"
                                  : "border-orange-200 dark:border-orange-800 bg-orange-50 dark:bg-orange-900/20 text-orange-600 dark:text-orange-300 hover:bg-orange-100 dark:hover:bg-orange-900/40"
                              }`}
                            >
                              {selected ? "✓ " : "🔖 "}
                              {kw}
                            </button>
                          );
                        })}
                      </div>
                    )}
                  </>
                );

                return (
                  <li key={p.id}>
                    {isClickable ? (
                      <Link
                        href={`/wiki/${encodeURIComponent(p.slug)}`}
                        className="block p-3 rounded border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 hover:border-orange-400 dark:hover:border-orange-500 transition"
                      >
                        {inner}
                      </Link>
                    ) : (
                      <div
                        className="block p-3 rounded border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 opacity-60 cursor-not-allowed"
                        title={`${ek} 상태 — 처리 완료 후 클릭 가능`}
                      >
                        {inner}
                      </div>
                    )}
                  </li>
                );
              })}
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
