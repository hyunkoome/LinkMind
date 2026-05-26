"use client";

import { useEffect, useState } from "react";

import {
  COLOR_GROUPS,
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

  if (collapsed) {
    return (
      <button
        type="button"
        onClick={() => setCollapsed(false)}
        className="pointer-events-auto text-[11px] px-2 py-1 bg-white/85 dark:bg-zinc-900/85 backdrop-blur rounded shadow-sm hover:bg-orange-100 dark:hover:bg-orange-900/30 self-start"
        title={t.graph.legend.toggle}
      >
        ❔ {t.graph.legend.toggle}
      </button>
    );
  }

  return (
    <aside className="pointer-events-auto w-64 max-h-[70vh] overflow-y-auto bg-white/95 dark:bg-zinc-900/95 backdrop-blur border border-zinc-200 dark:border-zinc-800 rounded shadow-lg text-xs self-start">
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
        {/* 카테고리 (키워드 노드) */}
        <section>
          <div className="text-[10px] uppercase tracking-wider text-zinc-500 mb-1">
            {locale === "ko" ? "카테고리" : "Category"}
          </div>
          <ul className="space-y-1">
            <li className="flex items-center gap-2">
              <span
                className="inline-block w-3 h-3 rounded-full shrink-0"
                style={{ backgroundColor: "#facc15" }}
              />
              <span className="text-zinc-700 dark:text-zinc-300">
                {locale === "ko" ? "키워드 카테고리 (auto)" : "keyword category (auto)"}
              </span>
            </li>
            <li className="flex items-center gap-2">
              <span
                className="inline-block w-3 h-3 rounded-full shrink-0"
                style={{ backgroundColor: "#fde047" }}
              />
              <span className="text-zinc-700 dark:text-zinc-300">
                {locale === "ko" ? "📌 즐겨찾기" : "📌 pinned"}
              </span>
            </li>
          </ul>
          <div className="mt-1 text-[10px] text-zinc-500">
            {locale === "ko"
              ? "크기 = topic 수"
              : "size = topic count"}
          </div>
        </section>

        {/* 그룹별 색상 — 같은 의미는 같은 색 계열 */}
        {COLOR_GROUPS.map((g) => {
          const sourceItems = g.sourceTypes.map((st) => ({
            color: SOURCE_TYPE_COLORS[st],
            label: SOURCE_TYPE_LABEL[st]?.[locale] || st,
            key: `s-${st}`,
          }));
          const topicItems = g.topicKinds.map((tk) => ({
            color: TOPIC_KIND_COLORS[tk],
            label: TOPIC_KIND_LABEL[tk]?.[locale] || tk,
            key: `t-${tk}`,
          }));
          const all = [...sourceItems, ...topicItems];
          if (all.length === 0) return null;
          return (
            <section key={g.key}>
              <div className="text-[10px] uppercase tracking-wider text-zinc-500 mb-1 flex items-center gap-1.5">
                <span
                  className="inline-block w-2.5 h-2.5 rounded-full"
                  style={{ backgroundColor: g.groupColor }}
                />
                {g.label[locale]}
              </div>
              <ul className="space-y-1 pl-1">
                {all.map((e) => (
                  <li key={e.key} className="flex items-center gap-2">
                    <span
                      className="inline-block w-2.5 h-2.5 rounded-full shrink-0"
                      style={{ backgroundColor: e.color }}
                    />
                    <span className="text-[11px] text-zinc-700 dark:text-zinc-300">
                      {e.label}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          );
        })}

        {/* 기타 cluster (외부 ID 없음) */}
        <section>
          <div className="text-[10px] uppercase tracking-wider text-zinc-500 mb-1">
            {locale === "ko" ? "기타" : "Other"}
          </div>
          <ul className="space-y-1">
            <li className="flex items-center gap-2">
              <span
                className="inline-block w-2.5 h-2.5 rounded-full shrink-0"
                style={{ backgroundColor: DEFAULT_TOPIC_COLOR }}
              />
              <span className="text-[11px] text-zinc-700 dark:text-zinc-300">
                {locale === "ko"
                  ? "외부 ID 없는 토픽 (orange)"
                  : "topic without external id (orange)"}
              </span>
            </li>
          </ul>
        </section>

        {/* 선택/관련 시각 효과 — 사용자 요구: 흰색 X, 사이즈 + 밝기로 강조 */}
        <section>
          <div className="text-[10px] uppercase tracking-wider text-zinc-500 mb-1">
            {locale === "ko" ? "선택 상태" : "Selection"}
          </div>
          <ul className="space-y-1.5 text-[11px] text-zinc-700 dark:text-zinc-300">
            <li className="flex items-center gap-2">
              <span className="inline-flex items-center gap-1">
                <span
                  className="inline-block w-4 h-4 rounded-full"
                  style={{ backgroundColor: "#10b981" }}
                />
              </span>
              <span>
                <span className="font-medium">
                  {locale === "ko" ? "선택" : "selected"}
                </span>
                {" — "}
                {locale === "ko"
                  ? "원래 색 + 1.7× 크게"
                  : "original color + 1.7× larger"}
              </span>
            </li>
            <li className="flex items-center gap-2">
              <span className="inline-flex items-center gap-1">
                <span
                  className="inline-block w-3 h-3 rounded-full"
                  style={{ backgroundColor: "#10b981" }}
                />
              </span>
              <span>
                <span className="font-medium">
                  {locale === "ko" ? "같은 묶음" : "related"}
                </span>
                {" — "}
                {locale === "ko"
                  ? "원래 색 + 1.3× 크게"
                  : "original color + 1.3× larger"}
              </span>
            </li>
            <li className="flex items-center gap-2">
              <span className="inline-flex items-center gap-1">
                <span
                  className="inline-block w-2 h-2 rounded-full"
                  style={{ backgroundColor: "#0a5a3f" }}
                />
              </span>
              <span>
                <span className="font-medium">
                  {locale === "ko" ? "무관" : "non-related"}
                </span>
                {" — "}
                {locale === "ko"
                  ? "어둡게 가라앉음"
                  : "darkened (faded)"}
              </span>
            </li>
          </ul>
        </section>

        {/* Item 표시 */}
        <section>
          <div className="text-[10px] uppercase tracking-wider text-zinc-500 mb-1">
            {locale === "ko" ? "아이템 표식" : "Item indicators"}
          </div>
          <ul className="space-y-1 text-[11px] text-zinc-700 dark:text-zinc-300">
            <li>{t.graph.legend.unread}</li>
            <li>{t.graph.legend.hasNotes}</li>
          </ul>
        </section>
      </div>
    </aside>
  );
}
