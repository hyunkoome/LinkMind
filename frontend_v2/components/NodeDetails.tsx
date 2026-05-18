"use client";

import { useEffect, useState } from "react";

import {
  getCategory,
  getTopic,
  type CategoryDetailResponse,
  type TopicDetailResponse,
} from "@/lib/api";
import { useT } from "@/lib/i18n/context";

interface NodeDetailsProps {
  /** "topic:<uuid>" 또는 "category:<uuid>" 를 받으면 그에 맞춰 detail 호출. item 은 ItemDetails 가 담당. */
  selectedNodeFullId: string | null;
  /** category fullId → slug 변환용 — page 가 보유한 graph 노드에서 추출 */
  resolveCategorySlug: (categoryFullId: string) => string | null;
  /** topic 안의 item 클릭 (raw uuid) / 카테고리 안의 topic 클릭 (raw uuid) — page 가 fullId 로 변환 */
  onItemClick: (itemId: string) => void;
  onTopicClick: (topicId: string) => void;
}

const COLLAPSED_LS_KEY = "linkmind:nodedetails-collapsed";

export default function NodeDetails({
  selectedNodeFullId,
  resolveCategorySlug,
  onItemClick,
  onTopicClick,
}: NodeDetailsProps) {
  const { t, locale } = useT();
  const [topic, setTopic] = useState<TopicDetailResponse | null>(null);
  const [category, setCategory] = useState<CategoryDetailResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [collapsed, setCollapsedState] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      const saved = window.localStorage.getItem(COLLAPSED_LS_KEY);
      if (saved === "1") setCollapsedState(true);
    } catch {
      /* ignore */
    }
  }, []);
  const setCollapsed = (v: boolean) => {
    setCollapsedState(v);
    try {
      window.localStorage.setItem(COLLAPSED_LS_KEY, v ? "1" : "0");
    } catch {
      /* ignore */
    }
  };

  // selectedNodeFullId 가 바뀌면 적절한 detail fetch.
  useEffect(() => {
    if (!selectedNodeFullId) {
      setTopic(null);
      setCategory(null);
      return;
    }
    if (selectedNodeFullId.startsWith("item:")) {
      // item 은 ItemDetails 가 담당 — NodeDetails 는 비활성
      setTopic(null);
      setCategory(null);
      return;
    }
    setLoading(true);
    setError(null);
    setCollapsed(false); // 새 선택이면 자동 펼침
    if (selectedNodeFullId.startsWith("topic:")) {
      const tid = selectedNodeFullId.replace(/^topic:/, "");
      setCategory(null);
      getTopic(tid)
        .then(setTopic)
        .catch((e) => setError((e as Error).message))
        .finally(() => setLoading(false));
    } else if (selectedNodeFullId.startsWith("category:")) {
      const slug = resolveCategorySlug(selectedNodeFullId);
      setTopic(null);
      if (!slug) {
        setLoading(false);
        setError(locale === "ko" ? "카테고리 slug 못 찾음" : "category slug not found");
        return;
      }
      getCategory(slug)
        .then(setCategory)
        .catch((e) => setError((e as Error).message))
        .finally(() => setLoading(false));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedNodeFullId]);

  // selectedNodeFullId 가 item 이면 자체 panel 비활성 (ItemDetails 가 그 자리에서 동작).
  if (!selectedNodeFullId || selectedNodeFullId.startsWith("item:")) {
    return null;
  }

  const apiBase =
    process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";

  if (collapsed) {
    return (
      <aside
        className="w-8 shrink-0 h-full border-l border-zinc-200 dark:border-zinc-800 bg-zinc-50 dark:bg-zinc-900 flex items-start justify-center cursor-pointer hover:bg-zinc-100 dark:hover:bg-zinc-800 transition"
        onClick={() => setCollapsed(false)}
        title={locale === "ko" ? "상세 보기" : "show details"}
      >
        <div className="mt-3 text-xs text-zinc-400 [writing-mode:vertical-rl] rotate-180">
          ⟨ {locale === "ko" ? "상세" : "details"}
        </div>
      </aside>
    );
  }

  const isCategory = selectedNodeFullId.startsWith("category:");

  return (
    <aside className="w-96 shrink-0 h-full overflow-y-auto border-l border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900">
      <header className="sticky top-0 bg-white dark:bg-zinc-900 border-b border-zinc-200 dark:border-zinc-800 p-3 flex items-center justify-between z-10">
        <div className="text-xs uppercase tracking-wider text-zinc-500">
          {isCategory
            ? locale === "ko" ? "카테고리" : "Category"
            : locale === "ko" ? "토픽" : "Topic"}
        </div>
        <button
          type="button"
          onClick={() => setCollapsed(true)}
          className="text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200 text-sm"
          title={locale === "ko" ? "패널 접기" : "collapse"}
        >
          ⟩
        </button>
      </header>

      {loading && (
        <div className="p-4 text-sm text-zinc-500">
          {t.itemDetails.loadingItem}
        </div>
      )}
      {error && (
        <div className="p-4 text-sm text-red-500">
          {t.common.error}: {error}
        </div>
      )}

      {/* Topic detail */}
      {topic && !loading && (
        <div className="p-4 space-y-4">
          <div>
            <h2 className="text-base font-semibold mb-1 break-words">
              {topic.title}
            </h2>
            <div className="text-[10px] font-mono text-zinc-500 break-all">
              {topic.slug}
            </div>
          </div>
          {topic.description && (
            <p className="text-xs text-zinc-700 dark:text-zinc-300 whitespace-pre-line">
              {topic.description}
            </p>
          )}
          {topic.tags.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {topic.tags.slice(0, 12).map((tag) => (
                <span
                  key={tag}
                  className="text-[10px] px-1.5 py-0.5 bg-zinc-100 dark:bg-zinc-800 rounded text-zinc-600 dark:text-zinc-400"
                >
                  #{tag}
                </span>
              ))}
            </div>
          )}
          <div>
            <div className="text-[10px] uppercase tracking-wider text-zinc-500 mb-1">
              {locale === "ko" ? "자료" : "Items"} ({topic.items.length})
            </div>
            <ul className="space-y-2">
              {topic.items.map((it) => {
                const externalUrl = it.source_url
                  ? it.source_url.startsWith("/")
                    ? `${apiBase}${it.source_url}`
                    : it.source_url
                  : null;
                return (
                  <li
                    key={it.id}
                    className="border border-zinc-200 dark:border-zinc-800 rounded p-2 bg-zinc-50 dark:bg-zinc-900/50"
                  >
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-[9px] px-1 py-0.5 rounded bg-zinc-200 dark:bg-zinc-700 text-zinc-700 dark:text-zinc-300">
                        {it.source_type}
                      </span>
                      <span className="text-[9px] text-zinc-500">{it.role}</span>
                    </div>
                    <button
                      type="button"
                      onClick={() => onItemClick(it.id)}
                      className="text-left text-sm text-zinc-900 dark:text-zinc-100 hover:text-orange-600 dark:hover:text-orange-400 break-words"
                    >
                      {it.title || "(no title)"}
                    </button>
                    {externalUrl && (
                      <div className="mt-1">
                        <a
                          href={externalUrl}
                          target="_blank"
                          rel="noreferrer noopener"
                          className="text-[10px] text-blue-600 dark:text-blue-400 hover:underline break-all"
                        >
                          {locale === "ko" ? "🔗 원본 열기 (새창)" : "🔗 open original (new tab)"}
                        </a>
                      </div>
                    )}
                    {it.summary && (
                      <p className="text-[11px] text-zinc-600 dark:text-zinc-400 mt-1 line-clamp-3">
                        {it.summary}
                      </p>
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        </div>
      )}

      {/* Category detail */}
      {category && !loading && (
        <div className="p-4 space-y-4">
          <div>
            <h2 className="text-base font-semibold mb-1 break-words">
              {category.pinned ? "📌 " : ""}
              {category.label}
            </h2>
            <div className="text-[10px] font-mono text-zinc-500 break-all">
              {category.slug}
            </div>
          </div>
          {category.description && (
            <p className="text-xs text-zinc-700 dark:text-zinc-300 whitespace-pre-line">
              {category.description}
            </p>
          )}
          {category.synonyms.length > 0 && (
            <div>
              <div className="text-[10px] uppercase tracking-wider text-zinc-500 mb-1">
                {locale === "ko" ? "동의어" : "Synonyms"}
              </div>
              <div className="flex flex-wrap gap-1">
                {category.synonyms.map((s) => (
                  <span
                    key={s}
                    className="text-[10px] px-1.5 py-0.5 bg-zinc-100 dark:bg-zinc-800 rounded text-zinc-600 dark:text-zinc-400"
                  >
                    {s}
                  </span>
                ))}
              </div>
            </div>
          )}
          <div>
            <div className="text-[10px] uppercase tracking-wider text-zinc-500 mb-1">
              {locale === "ko" ? "토픽" : "Topics"} ({category.topics.length})
            </div>
            <ul className="space-y-1">
              {category.topics.map((tp) => (
                <li key={tp.id}>
                  <button
                    type="button"
                    onClick={() => onTopicClick(tp.id)}
                    className="w-full text-left px-2 py-1.5 rounded text-xs hover:bg-zinc-100 dark:hover:bg-zinc-800 text-zinc-700 dark:text-zinc-300"
                    title={tp.slug}
                  >
                    <span className="block truncate">{tp.title}</span>
                    <span className="block text-[10px] text-zinc-500">
                      {tp.item_count} {t.topicsTree.itemsCount} · {tp.slug}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </aside>
  );
}
