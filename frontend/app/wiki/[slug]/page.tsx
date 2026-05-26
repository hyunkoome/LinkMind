"use client";

/**
 * D10 wave-1g — /wiki/[slug] — wiki page 상세.
 *
 * 흐름:
 *  1. mount 시 GET /wiki/{slug} 호출
 *     - body_status='empty' 또는 'stale' 면 backend 가 자동 eager 합성 (~10-20초)
 *  2. body markdown render (WikiBody)
 *  3. sources panel (오른쪽 column)
 *  4. cross-links (있으면)
 *  5. regenerate 버튼 — POST /wiki/{slug}/regenerate
 */

import Link from "next/link";
import { use, useEffect, useState } from "react";

import KeywordsEditor from "@/components/wiki/KeywordsEditor";
import WikiBody from "@/components/wiki/WikiBody";
import {
  getWikiPage,
  regenerateWikiPage,
  type WikiPageDetail,
  type WikiSource,
} from "@/lib/api";

const STATUS_COLORS: Record<string, string> = {
  ready:
    "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400",
  stale: "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400",
  generating:
    "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400 animate-pulse",
  empty: "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400",
};

interface PageProps {
  // Next.js 16: params 는 Promise — `use()` 로 unwrap.
  params: Promise<{ slug: string }>;
}

export default function WikiDetailPage({ params }: PageProps) {
  const { slug } = use(params);
  const [page, setPage] = useState<WikiPageDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [regenerating, setRegenerating] = useState(false);

  const load = (regenerate = false) => {
    setLoading(true);
    setError(null);
    getWikiPage(slug, { regenerate })
      .then((p) => setPage(p))
      .catch((e) => setError((e as Error).message))
      .finally(() => setLoading(false));
  };

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => load(false), [slug]);

  const onRegenerate = async () => {
    setRegenerating(true);
    try {
      await regenerateWikiPage(slug);
      load(false);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRegenerating(false);
    }
  };

  if (loading && !page) {
    return (
      <div className="flex-1 flex items-center justify-center text-zinc-500">
        wiki page 로딩 중… (첫 생성이면 LLM 합성 10-20초 소요)
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex-1 p-6">
        <Link href="/wiki" className="text-xs text-orange-600 hover:underline">
          ← Wiki 목록으로
        </Link>
        <div className="mt-4 p-4 rounded bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-300">
          에러: {error}
        </div>
      </div>
    );
  }

  if (!page) {
    return <div className="flex-1 p-6 text-zinc-500">페이지 없음</div>;
  }

  return (
    <div className="flex-1 overflow-auto bg-zinc-50 dark:bg-zinc-950">
      <div className="max-w-6xl mx-auto p-6">
        {/* breadcrumb */}
        <div className="flex items-center justify-between mb-4">
          <Link
            href="/wiki"
            className="text-xs text-orange-600 dark:text-orange-400 hover:underline"
          >
            ← Wiki 목록
          </Link>
          <div className="flex items-center gap-2">
            <span
              className={`text-[10px] px-2 py-0.5 rounded ${STATUS_COLORS[page.body_status] || STATUS_COLORS.empty}`}
            >
              {page.body_status}
            </span>
            <span className="text-[10px] text-zinc-500">
              v{page.latest_version}
            </span>
            <button
              type="button"
              onClick={onRegenerate}
              disabled={regenerating}
              className="text-xs px-2 py-1 rounded border border-orange-300 dark:border-orange-700 text-orange-700 dark:text-orange-400 hover:bg-orange-50 dark:hover:bg-orange-900/20 disabled:opacity-50"
            >
              {regenerating ? "재합성 중…" : "🔄 재합성"}
            </button>
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-[1fr_320px] gap-6">
          {/* Body */}
          <article className="bg-white dark:bg-zinc-900 rounded border border-zinc-200 dark:border-zinc-800 p-6">
            <WikiBody body={page.body || ""} />
          </article>

          {/* aside — 사용자 요청 순서 (2026-05-26): Sources → Relationship → Keywords */}
          <aside className="space-y-4">
            {/* 1) Sources */}
            <section className="bg-white dark:bg-zinc-900 rounded border border-zinc-200 dark:border-zinc-800 p-4">
              <h3 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100 mb-2">
                Sources ({page.sources.length})
              </h3>
              <ul className="space-y-2">
                {page.sources.map((s: WikiSource, i: number) => (
                  <li
                    key={s.item_id}
                    id={`source-${i + 1}`}
                    className="text-xs text-zinc-700 dark:text-zinc-300"
                  >
                    <span className="text-zinc-400 mr-1">[{i + 1}]</span>
                    {s.source_url ? (
                      <a
                        href={s.source_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-blue-600 dark:text-blue-400 hover:underline"
                      >
                        {s.title || s.item_id.slice(0, 8)}
                      </a>
                    ) : (
                      <span>{s.title || s.item_id.slice(0, 8)}</span>
                    )}
                    <div className="text-[10px] text-zinc-500 mt-0.5">
                      {s.source_type}
                      {s.confidence !== null &&
                        ` · conf=${s.confidence.toFixed(2)}`}
                      {s.role && ` · ${s.role}`}
                    </div>
                  </li>
                ))}
              </ul>
            </section>

            {/* 2) Relationship (옛 Cross-links) — 비어도 섹션 표시 (사용자 명시 2026-05-26) */}
            <section className="bg-white dark:bg-zinc-900 rounded border border-zinc-200 dark:border-zinc-800 p-4">
              <h3 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100 mb-2">
                Relationship ({page.cross_links.length})
              </h3>
              {page.cross_links.length === 0 ? (
                <p className="text-[11px] text-zinc-500 dark:text-zinc-400 italic">
                  (관련 wiki 페이지 없음 — 다른 wiki 가 같은 자료 공유하면 자동 link)
                </p>
              ) : (
                <ul className="space-y-1">
                  {page.cross_links.map((c) => (
                    <li key={c.slug} className="text-xs">
                      <Link
                        href={`/wiki/${encodeURIComponent(c.slug)}`}
                        className="text-orange-600 dark:text-orange-400 hover:underline"
                      >
                        {c.title || c.slug}
                      </Link>
                      <span className="text-zinc-400 ml-1">
                        ({c.shared_items} shared)
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            {/* 3) Keywords (편집 가능) */}
            <KeywordsEditor
              slug={page.slug}
              keywords={page.keywords || []}
              onChange={(next) => setPage({ ...page, keywords: next })}
            />

            <section className="bg-zinc-100 dark:bg-zinc-800 rounded p-3 text-[10px] text-zinc-500 dark:text-zinc-400 space-y-1">
              <div>
                <span className="font-medium">slug:</span>{" "}
                <span className="font-mono">{page.slug}</span>
              </div>
              {page.body_model && (
                <div>
                  <span className="font-medium">model:</span> {page.body_model}
                </div>
              )}
              <div>
                <span className="font-medium">version:</span> {page.latest_version}
              </div>
              {page.body_generated_at && (
                <div>
                  <span className="font-medium">generated:</span>{" "}
                  {new Date(page.body_generated_at).toLocaleString()}
                </div>
              )}
            </section>
          </aside>
        </div>
      </div>
    </div>
  );
}
