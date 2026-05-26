"use client";

import { useT } from "@/lib/i18n/context";
import type { ItemListFacets, ItemListFilters } from "@/lib/api";

const KIND_LABELS: Record<string, { ko: string; en: string }> = {
  none: { ko: "정상 (cleanup 불필요)", en: "Normal" },
  image_no_ocr: { ko: "이미지 (OCR 미처리)", en: "Image (no OCR)" },
  extraction_failed: { ko: "본문 추출 실패", en: "Extraction failed" },
  binary_no_extract: { ko: "바이너리 파일", en: "Binary file" },
  short_raw: { ko: "본문 거의 빈", en: "Short raw" },
};

interface Props {
  filters: ItemListFilters;
  facets: ItemListFacets;
  total: number;
  onChange: (filters: ItemListFilters) => void;
}

export default function FilterSidebar({ filters, facets, total, onChange }: Props) {
  const { locale, t } = useT();

  const set = (k: keyof ItemListFilters, v: unknown) =>
    onChange({ ...filters, [k]: v === filters[k] ? undefined : v, page: 1 });

  const clear = () => onChange({ sort: filters.sort, page: 1, page_size: filters.page_size });

  const hasActive = Boolean(
    filters.kind || filters.source_type || filters.domain ||
    filters.has_user_notes !== undefined || filters.has_summary !== undefined ||
    filters.q,
  );

  return (
    <aside className="w-64 shrink-0 h-full overflow-y-auto border-r border-zinc-200 dark:border-zinc-800 bg-zinc-50 dark:bg-zinc-950 p-3 text-xs">
      <div className="flex items-baseline justify-between mb-3">
        <h2 className="text-sm font-semibold">
          {locale === "ko" ? "필터" : "Filters"}
        </h2>
        {hasActive && (
          <button
            type="button"
            onClick={clear}
            className="text-[10px] text-orange-600 dark:text-orange-400 hover:underline"
          >
            {locale === "ko" ? "초기화" : "Clear"}
          </button>
        )}
      </div>

      <div className="mb-2 text-[10px] text-zinc-500">
        {locale === "ko" ? "결과" : "Total"}: <span className="font-mono font-medium text-zinc-700 dark:text-zinc-300">{total.toLocaleString()}</span>
      </div>

      {/* Search */}
      <div className="mb-4">
        <input
          type="text"
          value={filters.q || ""}
          onChange={(e) => onChange({ ...filters, q: e.target.value || undefined, page: 1 })}
          placeholder={locale === "ko" ? "제목/본문/메모 검색…" : "Search title/raw/notes…"}
          className="w-full px-2 py-1.5 text-xs bg-white dark:bg-zinc-900 border border-zinc-300 dark:border-zinc-700 rounded focus:outline-none focus:ring-1 focus:ring-orange-500"
        />
      </div>

      {/* fetch_error_kind */}
      <FilterGroup title={locale === "ko" ? "분류 사유" : "Issue kind"}>
        {Object.entries(facets.kind).map(([k, n]) => (
          <FilterItem
            key={k}
            label={KIND_LABELS[k]?.[locale] || k}
            count={n}
            active={filters.kind === k}
            onClick={() => set("kind", k)}
          />
        ))}
      </FilterGroup>

      {/* source_type */}
      <FilterGroup title={locale === "ko" ? "자료 형식" : "Source type"}>
        {Object.entries(facets.source_type).map(([k, n]) => (
          <FilterItem
            key={k}
            label={k}
            count={n}
            active={filters.source_type === k}
            onClick={() => set("source_type", k)}
          />
        ))}
      </FilterGroup>

      {/* domain */}
      <FilterGroup title={locale === "ko" ? "도메인" : "Domain"}>
        {Object.entries(facets.domain).map(([k, n]) => (
          <FilterItem
            key={k}
            label={k}
            count={n}
            active={filters.domain === k}
            onClick={() => set("domain", k)}
          />
        ))}
      </FilterGroup>

      {/* flags */}
      <FilterGroup title={locale === "ko" ? "상태" : "Flags"}>
        <FilterItem
          label={locale === "ko" ? "메모 있음" : "Has notes"}
          active={filters.has_user_notes === true}
          onClick={() => set("has_user_notes", filters.has_user_notes === true ? undefined : true)}
        />
        <FilterItem
          label={locale === "ko" ? "메모 없음" : "No notes"}
          active={filters.has_user_notes === false}
          onClick={() => set("has_user_notes", filters.has_user_notes === false ? undefined : false)}
        />
        <FilterItem
          label={locale === "ko" ? "요약 있음" : "Has summary"}
          active={filters.has_summary === true}
          onClick={() => set("has_summary", filters.has_summary === true ? undefined : true)}
        />
        <FilterItem
          label={locale === "ko" ? "요약 없음" : "No summary"}
          active={filters.has_summary === false}
          onClick={() => set("has_summary", filters.has_summary === false ? undefined : false)}
        />
      </FilterGroup>

      <div className="text-[10px] text-zinc-400 italic mt-4">
        {locale === "ko"
          ? "필터 클릭 시 그 사유/형식/도메인만 보기. 다시 클릭하면 해제."
          : "Click a filter to drill down. Click again to clear."}
      </div>
    </aside>
  );
}

function FilterGroup({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mb-3">
      <div className="text-[10px] uppercase text-zinc-500 mb-1 font-medium tracking-wider">
        {title}
      </div>
      <ul className="space-y-0.5">{children}</ul>
    </div>
  );
}

function FilterItem({
  label, count, active, onClick,
}: { label: string; count?: number; active: boolean; onClick: () => void }) {
  return (
    <li>
      <button
        type="button"
        onClick={onClick}
        className={`w-full text-left flex items-center justify-between gap-2 px-2 py-1 rounded transition text-[11px] ${
          active
            ? "bg-orange-100 dark:bg-orange-900/30 text-orange-700 dark:text-orange-300 font-medium"
            : "hover:bg-zinc-100 dark:hover:bg-zinc-800 text-zinc-700 dark:text-zinc-300"
        }`}
      >
        <span className="truncate">{label}</span>
        {count !== undefined && (
          <span className="font-mono text-[10px] text-zinc-500 shrink-0">
            {count.toLocaleString()}
          </span>
        )}
      </button>
    </li>
  );
}
