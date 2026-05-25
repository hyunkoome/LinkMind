"use client";

import { useCallback, useEffect, useState } from "react";

import ActionPanel from "@/components/cleanup/ActionPanel";
import FilterSidebar from "@/components/cleanup/FilterSidebar";
import ItemCard from "@/components/cleanup/ItemCard";
import { listItems, type ItemListFilters, type ItemListResponse } from "@/lib/api";
import { useT } from "@/lib/i18n/context";

const EMPTY_FACETS = { kind: {}, source_type: {}, domain: {} };
const PAGE_SIZE = 50;

export default function CleanupPage() {
  const { locale, t } = useT();
  const [filters, setFilters] = useState<ItemListFilters>({
    sort: "ingested_at_desc",
    page: 1,
    page_size: PAGE_SIZE,
  });
  const [response, setResponse] = useState<ItemListResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [refreshTick, setRefreshTick] = useState(0);

  // q 검색 debounce — 입력 도중 fetch 폭주 방지
  const [debouncedFilters, setDebouncedFilters] = useState(filters);
  useEffect(() => {
    const id = setTimeout(() => setDebouncedFilters(filters), 250);
    return () => clearTimeout(id);
  }, [filters]);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    listItems(debouncedFilters)
      .then((r) => { if (alive) setResponse(r); })
      .catch((e) => { if (alive) setError((e as Error).message); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [debouncedFilters, refreshTick]);

  const handleUpdated = useCallback(() => {
    // 액션 (메모 추가, 카테고리 link) 후 list 새로고침 — 카드 preview 갱신
    setRefreshTick((t) => t + 1);
  }, []);

  const totalPages = response ? Math.max(1, Math.ceil(response.total / PAGE_SIZE)) : 1;
  const page = filters.page || 1;

  const selectedCard = response?.items.find((it) => it.id === selectedId) || null;

  return (
    <div className="flex h-full">
      <FilterSidebar
        filters={filters}
        facets={response?.facets || EMPTY_FACETS}
        total={response?.total || 0}
        onChange={(f) => {
          setFilters(f);
          setSelectedId(null);
        }}
      />

      <main className="flex-1 h-full overflow-y-auto p-4">
        <div className="flex items-baseline justify-between mb-3 gap-3">
          <div>
            <h1 className="text-lg font-semibold">
              🧹 {locale === "ko" ? "정리 필요한 자료" : "Cleanup queue"}
            </h1>
            <p className="text-xs text-zinc-500">
              {locale === "ko"
                ? "raw + URL 은 보존됨 — 본문 직접 붙여넣기 / 메모 / 카테고리 수동 보강"
                : "Raw + URL preserved — manually paste content / add note / link category"}
            </p>
          </div>
          <select
            value={filters.sort || "ingested_at_desc"}
            onChange={(e) => setFilters({ ...filters, sort: e.target.value, page: 1 })}
            className="px-2 py-1 text-xs bg-white dark:bg-zinc-900 border border-zinc-300 dark:border-zinc-700 rounded"
          >
            <option value="ingested_at_desc">
              {locale === "ko" ? "최근 추가순" : "Recent first"}
            </option>
            <option value="ingested_at_asc">
              {locale === "ko" ? "오래된순" : "Oldest first"}
            </option>
            <option value="domain">
              {locale === "ko" ? "도메인순" : "By domain"}
            </option>
            <option value="kind">
              {locale === "ko" ? "사유순" : "By kind"}
            </option>
          </select>
        </div>

        {error && (
          <div className="mb-3 p-2 text-xs bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-300 rounded">
            {t.common.error}: {error}
          </div>
        )}

        {loading && !response && (
          <div className="text-center text-xs text-zinc-500 py-12">
            {t.common.loading}
          </div>
        )}

        {response && response.items.length === 0 && !loading && (
          <div className="text-center text-xs text-zinc-500 py-12">
            {locale === "ko" ? "조건에 맞는 자료가 없습니다" : "No items match filters"}
          </div>
        )}

        {response && response.items.length > 0 && (
          <>
            {/* kind=image_no_ocr 필터 적용 시 grid 레이아웃 (썸네일 시각 인지가 우선).
                다른 경우는 list 레이아웃 (텍스트 가독성 우선). */}
            <ul
              className={
                filters.kind === "image_no_ocr"
                  ? "grid grid-cols-2 xl:grid-cols-3 gap-2"
                  : "space-y-2"
              }
            >
              {response.items.map((item) => (
                <ItemCard
                  key={item.id}
                  item={item}
                  selected={item.id === selectedId}
                  onSelect={() => setSelectedId(item.id)}
                />
              ))}
            </ul>

            {/* Pagination */}
            <div className="mt-4 flex items-center justify-center gap-3 text-xs">
              <button
                type="button"
                onClick={() => setFilters({ ...filters, page: Math.max(1, page - 1) })}
                disabled={page <= 1 || loading}
                className="px-3 py-1 bg-white dark:bg-zinc-900 border border-zinc-300 dark:border-zinc-700 rounded disabled:opacity-40"
              >
                ← {locale === "ko" ? "이전" : "Prev"}
              </button>
              <span className="text-zinc-500 font-mono">
                {page} / {totalPages}
              </span>
              <button
                type="button"
                onClick={() => setFilters({ ...filters, page: Math.min(totalPages, page + 1) })}
                disabled={page >= totalPages || loading}
                className="px-3 py-1 bg-white dark:bg-zinc-900 border border-zinc-300 dark:border-zinc-700 rounded disabled:opacity-40"
              >
                {locale === "ko" ? "다음" : "Next"} →
              </button>
            </div>
          </>
        )}
      </main>

      <ActionPanel
        card={selectedCard}
        onUpdated={handleUpdated}
        onDeleted={() => {
          // 삭제 후 패널 닫고 list 새로고침. 같은 페이지에 머무름.
          setSelectedId(null);
          setRefreshTick((t) => t + 1);
        }}
        onClose={() => setSelectedId(null)}
      />
    </div>
  );
}
