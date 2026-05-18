"use client";

import { useEffect, useState } from "react";

import {
  DEFAULT_TOPIC_COLOR,
  SOURCE_TYPE_COLORS,
  SOURCE_TYPE_LABEL,
  TOPIC_KIND_COLORS,
  TOPIC_KIND_LABEL,
} from "@/lib/colors";
import { useT } from "@/lib/i18n/context";

const LS_KEY = "linkmind:legend-collapsed";

export default function Legend() {
  const { t, locale } = useT();
  const [collapsed, setCollapsedState] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      const saved = window.localStorage.getItem(LS_KEY);
      if (saved === "1") setCollapsedState(true);
    } catch {
      /* ignore */
    }
  }, []);

  const setCollapsed = (v: boolean) => {
    setCollapsedState(v);
    try {
      window.localStorage.setItem(LS_KEY, v ? "1" : "0");
    } catch {
      /* ignore */
    }
  };

  // 접힘 상태 — 작은 chevron 버튼만
  if (collapsed) {
    return (
      <button
        type="button"
        onClick={() => setCollapsed(false)}
        className="absolute top-3 right-3 z-10 text-xs px-2 py-1 bg-white/80 dark:bg-zinc-900/80 backdrop-blur rounded shadow-sm hover:bg-orange-100 dark:hover:bg-orange-900/30"
        title={t.graph.legend.toggle}
      >
        ❔ {t.graph.legend.toggle}
      </button>
    );
  }

  // topic kinds — entries
  const topicEntries: Array<[string, string, string]> = [
    // [color, label key, key for label dict]
    ...Object.entries(TOPIC_KIND_COLORS).map(
      ([k, color]) =>
        [color, TOPIC_KIND_LABEL[k]?.[locale] || k, k] as [string, string, string],
    ),
    [DEFAULT_TOPIC_COLOR, locale === "ko" ? "기타 cluster" : "other cluster", "default"],
  ];

  const itemEntries: Array<[string, string]> = Object.entries(SOURCE_TYPE_COLORS).map(
    ([k, color]) => [color, SOURCE_TYPE_LABEL[k]?.[locale] || k],
  );

  return (
    <aside className="absolute top-3 right-3 z-10 w-64 max-h-[70vh] overflow-y-auto bg-white/95 dark:bg-zinc-900/95 backdrop-blur border border-zinc-200 dark:border-zinc-800 rounded shadow-lg text-xs">
      <header className="sticky top-0 px-3 py-2 border-b border-zinc-200 dark:border-zinc-800 flex items-center justify-between bg-white/95 dark:bg-zinc-900/95 backdrop-blur">
        <span className="font-medium text-zinc-700 dark:text-zinc-200">
          {t.graph.legend.title}
        </span>
        <button
          type="button"
          onClick={() => setCollapsed(true)}
          className="text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200 text-sm leading-none"
          aria-label={t.common.close}
        >
          ✕
        </button>
      </header>

      <div className="p-3 space-y-3">
        {/* Topic */}
        <section>
          <div className="text-[10px] uppercase tracking-wider text-zinc-500 mb-1">
            {t.graph.legend.topicSection}
          </div>
          <ul className="space-y-1">
            {topicEntries.map(([color, label, key]) => (
              <li key={`topic-${key}`} className="flex items-center gap-2">
                <span
                  className="inline-block w-3 h-3 rounded-full shrink-0"
                  style={{ backgroundColor: color }}
                />
                <span className="text-zinc-700 dark:text-zinc-300">{label}</span>
              </li>
            ))}
          </ul>
          <div className="mt-1 text-[10px] text-zinc-500">{t.graph.legend.size}</div>
        </section>

        {/* Item */}
        <section>
          <div className="text-[10px] uppercase tracking-wider text-zinc-500 mb-1">
            {t.graph.legend.itemSection}
          </div>
          <ul className="space-y-1">
            {itemEntries.map(([color, label]) => (
              <li key={`item-${label}`} className="flex items-center gap-2">
                <span
                  className="inline-block w-3 h-3 rounded shrink-0"
                  style={{ backgroundColor: color }}
                />
                <span className="text-zinc-700 dark:text-zinc-300">{label}</span>
              </li>
            ))}
          </ul>
        </section>

        {/* Indicators */}
        <section>
          <div className="text-[10px] uppercase tracking-wider text-zinc-500 mb-1">
            {t.graph.legend.indicatorSection}
          </div>
          <ul className="space-y-1 text-zinc-700 dark:text-zinc-300">
            <li>{t.graph.legend.unread}</li>
            <li>{t.graph.legend.hasNotes}</li>
            <li>{t.graph.legend.selected}</li>
          </ul>
        </section>
      </div>
    </aside>
  );
}
