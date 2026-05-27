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
import { useRouter } from "next/navigation";
import { use, useEffect, useState } from "react";

import KeywordsEditor from "@/components/wiki/KeywordsEditor";
import WikiBody from "@/components/wiki/WikiBody";
import {
  deleteWikiPage,
  getWikiPage,
  regenerateWikiPage,
  updateWikiPage,
  type WikiPageDeleteResponse,
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
  const router = useRouter();
  const [page, setPage] = useState<WikiPageDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [regenerating, setRegenerating] = useState(false);

  // 편집 모드 (2026-05-27) — title / description / body 수동 수정
  const [editing, setEditing] = useState(false);
  const [editTitle, setEditTitle] = useState("");
  const [editDescription, setEditDescription] = useState("");
  const [editBody, setEditBody] = useState("");
  const [saving, setSaving] = useState(false);

  // 삭제 confirm (2단계) — wiki + 연결 items 영구 삭제
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteResult, setDeleteResult] = useState<WikiPageDeleteResponse | null>(null);

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

  const onStartEdit = () => {
    if (!page) return;
    setEditTitle(page.title);
    setEditDescription(page.description || "");
    setEditBody(page.body || "");
    setEditing(true);
    setConfirmDelete(false);
  };

  const onCancelEdit = () => {
    setEditing(false);
    setEditTitle("");
    setEditDescription("");
    setEditBody("");
  };

  const onSaveEdit = async () => {
    if (!page) return;
    // 변경된 필드만 patch (없으면 null 그대로 — backend 가 COALESCE)
    const payload: { title?: string; description?: string; body?: string } = {};
    if (editTitle !== page.title) payload.title = editTitle;
    if (editDescription !== (page.description || "")) payload.description = editDescription;
    if (editBody !== (page.body || "")) payload.body = editBody;

    if (Object.keys(payload).length === 0) {
      setEditing(false);
      return;
    }

    setSaving(true);
    setError(null);
    try {
      const updated = await updateWikiPage(slug, payload);
      setPage(updated);
      setEditing(false);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const onConfirmDelete = async () => {
    setDeleting(true);
    setError(null);
    try {
      const result = await deleteWikiPage(slug);
      setDeleteResult(result);
      // 1.5초 후 wiki 목록으로 이동 (사용자가 결과 메시지 읽을 시간)
      setTimeout(() => router.push("/wiki"), 1500);
    } catch (e) {
      setError((e as Error).message);
      setDeleting(false);
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
            {!editing && (
              <>
                <button
                  type="button"
                  onClick={onStartEdit}
                  disabled={regenerating || deleting}
                  className="text-xs px-2 py-1 rounded border border-blue-300 dark:border-blue-700 text-blue-700 dark:text-blue-400 hover:bg-blue-50 dark:hover:bg-blue-900/20 disabled:opacity-50"
                >
                  ✏️ 편집
                </button>
                <button
                  type="button"
                  onClick={onRegenerate}
                  disabled={regenerating || deleting}
                  className="text-xs px-2 py-1 rounded border border-orange-300 dark:border-orange-700 text-orange-700 dark:text-orange-400 hover:bg-orange-50 dark:hover:bg-orange-900/20 disabled:opacity-50"
                >
                  {regenerating ? "재합성 중…" : "🔄 재합성"}
                </button>
              </>
            )}
          </div>
        </div>

        {/* 삭제 결과 메시지 (1.5초 표시 후 /wiki 로 redirect) */}
        {deleteResult && (
          <div className="mb-4 p-4 rounded bg-emerald-50 dark:bg-emerald-900/20 border border-emerald-300 dark:border-emerald-800 text-emerald-800 dark:text-emerald-200">
            <div className="font-medium">
              ✓ 위키 페이지 영구 삭제 완료
            </div>
            <div className="text-xs mt-1">
              연결된 자료 {deleteResult.deleted_items_count}개 삭제 ·
              영향받은 다른 위키 {deleteResult.affected_other_wikis_count}개 ·
              Wiki 목록으로 돌아갑니다…
            </div>
          </div>
        )}

        <div className="grid grid-cols-1 lg:grid-cols-[1fr_320px] gap-6">
          {/* Body — view mode 면 markdown render, edit mode 면 form */}
          {editing ? (
            <article className="bg-white dark:bg-zinc-900 rounded border border-blue-300 dark:border-blue-700 p-6">
              <div className="text-xs text-blue-700 dark:text-blue-400 font-medium mb-3">
                ✏️ 편집 모드 — markdown 자유롭게 수정. body 변경 시 새 버전이 적립됩니다.
              </div>

              <label className="block text-xs font-medium text-zinc-700 dark:text-zinc-300 mb-1">
                Title
              </label>
              <input
                type="text"
                value={editTitle}
                onChange={(e) => setEditTitle(e.target.value)}
                disabled={saving}
                className="w-full px-3 py-2 mb-4 text-sm rounded border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-950 text-zinc-900 dark:text-zinc-100 focus:outline-none focus:border-blue-500 disabled:opacity-50"
              />

              <label className="block text-xs font-medium text-zinc-700 dark:text-zinc-300 mb-1">
                Description (선택)
              </label>
              <textarea
                value={editDescription}
                onChange={(e) => setEditDescription(e.target.value)}
                disabled={saving}
                rows={2}
                className="w-full px-3 py-2 mb-4 text-sm rounded border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-950 text-zinc-900 dark:text-zinc-100 focus:outline-none focus:border-blue-500 disabled:opacity-50"
              />

              <label className="block text-xs font-medium text-zinc-700 dark:text-zinc-300 mb-1">
                Body (markdown — 5섹션 자유 수정)
              </label>
              <textarea
                value={editBody}
                onChange={(e) => setEditBody(e.target.value)}
                disabled={saving}
                rows={24}
                spellCheck={false}
                className="w-full px-3 py-2 mb-4 text-sm font-mono rounded border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-950 text-zinc-900 dark:text-zinc-100 focus:outline-none focus:border-blue-500 disabled:opacity-50"
              />

              <div className="flex gap-2 justify-end">
                <button
                  type="button"
                  onClick={onCancelEdit}
                  disabled={saving}
                  className="px-4 py-1.5 text-xs rounded border border-zinc-300 dark:border-zinc-700 text-zinc-700 dark:text-zinc-300 hover:bg-zinc-100 dark:hover:bg-zinc-800 disabled:opacity-50"
                >
                  취소
                </button>
                <button
                  type="button"
                  onClick={onSaveEdit}
                  disabled={saving}
                  className="px-4 py-1.5 text-xs rounded bg-blue-600 hover:bg-blue-700 text-white font-medium disabled:opacity-50"
                >
                  {saving ? "저장 중…" : "✓ 저장"}
                </button>
              </div>
            </article>
          ) : (
            <article className="bg-white dark:bg-zinc-900 rounded border border-zinc-200 dark:border-zinc-800 p-6">
              <WikiBody body={page.body || ""} />
            </article>
          )}

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

            {/* 영구 삭제 — wiki + 연결 items (raw DB) 모두 제거. irreversible.
                2단계 confirm — 첫 클릭은 경고 박스, 두 번째 클릭으로 확정 (D12 패턴). */}
            {!editing && !deleteResult && (
              <section className="bg-white dark:bg-zinc-900 rounded border border-red-200 dark:border-red-900 p-4">
                <div className="text-[11px] font-medium text-red-700 dark:text-red-400 mb-2">
                  ⚠ 영구 삭제 (irreversible)
                </div>
                {!confirmDelete ? (
                  <button
                    type="button"
                    onClick={() => setConfirmDelete(true)}
                    disabled={deleting}
                    className="w-full px-3 py-1.5 text-xs bg-white dark:bg-zinc-900 border border-red-300 dark:border-red-800 text-red-700 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20 rounded disabled:opacity-50"
                  >
                    🗑 위키 페이지 삭제…
                  </button>
                ) : (
                  <div className="p-3 text-[11px] bg-red-50 dark:bg-red-900/20 border border-red-300 dark:border-red-800 rounded space-y-2">
                    <div className="font-medium text-red-800 dark:text-red-200">
                      정말 삭제하시겠습니까? 영구 삭제입니다.
                    </div>
                    <div className="text-zinc-700 dark:text-zinc-300 space-y-1">
                      <div>
                        <span className="font-medium">{page.title}</span>
                      </div>
                      <div className="text-[10px] text-zinc-500 dark:text-zinc-400">
                        • 이 위키 페이지 삭제<br />
                        • 연결된 자료 (raw DB) <span className="font-medium text-red-700 dark:text-red-300">{page.sources.length}개</span> 영구 삭제<br />
                        • 다른 위키와 공유된 자료도 함께 삭제됨 → 그 위키의 sources 에서도 자동 제거<br />
                        • 다시 텔레그램에 입력하면 새로 추가할 수 있습니다
                      </div>
                    </div>
                    <div className="flex gap-1.5">
                      <button
                        type="button"
                        onClick={() => setConfirmDelete(false)}
                        disabled={deleting}
                        className="flex-1 px-2 py-1 text-[11px] bg-white dark:bg-zinc-900 border border-zinc-300 dark:border-zinc-700 rounded hover:bg-zinc-100 dark:hover:bg-zinc-800"
                      >
                        취소
                      </button>
                      <button
                        type="button"
                        onClick={onConfirmDelete}
                        disabled={deleting}
                        className="flex-1 px-2 py-1 text-[11px] bg-red-600 hover:bg-red-700 text-white rounded disabled:opacity-50 font-medium"
                      >
                        {deleting ? "삭제 중…" : "삭제 확정"}
                      </button>
                    </div>
                  </div>
                )}
              </section>
            )}
          </aside>
        </div>
      </div>
    </div>
  );
}
