"use client";

import { useEffect, useState } from "react";

import { fileUrl, getItem, patchItem } from "@/lib/api";
import type { ItemAttachment, ItemDetail } from "@/types/graph";

interface ItemDetailsProps {
  itemId: string | null;
  onClose: () => void;
}

// Modality 별 view 분기 — source_type 기준
type Modality = "pdf" | "youtube" | "github" | "url" | "document" | "telegram" | "note" | "other";

function modalityOf(source_type: string): Modality {
  if (source_type === "pdf") return "pdf";
  if (source_type === "youtube" || source_type === "youtube_playlist") return "youtube";
  if (source_type === "github") return "github";
  if (source_type === "url" || source_type === "arxiv") return "url";
  if (source_type === "document") return "document";
  if (source_type === "telegram") return "telegram";
  return "other";
}

export default function ItemDetails({ itemId, onClose }: ItemDetailsProps) {
  const [item, setItem] = useState<ItemDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notesDraft, setNotesDraft] = useState<string>("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!itemId) {
      setItem(null);
      setError(null);
      return;
    }
    setLoading(true);
    setError(null);
    getItem(itemId)
      .then((it) => {
        setItem(it);
        setNotesDraft(it.user_notes || "");
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [itemId]);

  const toggleRead = async () => {
    if (!item) return;
    setSaving(true);
    try {
      const updated = await patchItem(item.id, { is_read: !item.is_read });
      setItem(updated);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const saveNotes = async () => {
    if (!item) return;
    if (notesDraft === (item.user_notes || "")) return;
    setSaving(true);
    try {
      const updated = await patchItem(item.id, { user_notes: notesDraft });
      setItem(updated);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  if (!itemId) {
    return (
      <aside className="w-96 shrink-0 h-full border-l border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 flex items-center justify-center text-sm text-zinc-400 p-6 text-center">
        <div>
          노드를 클릭하면<br />item 상세가 표시됩니다
        </div>
      </aside>
    );
  }

  return (
    <aside className="w-96 shrink-0 h-full overflow-y-auto border-l border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900">
      <header className="sticky top-0 bg-white dark:bg-zinc-900 border-b border-zinc-200 dark:border-zinc-800 p-3 flex items-center justify-between z-10">
        <div className="text-xs uppercase tracking-wider text-zinc-500">
          {item ? `${item.source_type} · ${item.is_read ? "✓ read" : "● unread"}` : "loading…"}
        </div>
        <button
          type="button"
          onClick={onClose}
          className="text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200 text-lg leading-none"
          aria-label="닫기"
        >
          ✕
        </button>
      </header>

      {loading && <div className="p-4 text-sm text-zinc-500">불러오는 중…</div>}
      {error && (
        <div className="p-4 text-sm text-red-500">에러: {error}</div>
      )}

      {item && (
        <div className="p-4 space-y-4">
          {/* title + URL */}
          <div>
            <h2 className="text-base font-semibold mb-1 break-words">
              {item.title || "(no title)"}
            </h2>
            {item.source_url && (
              <a
                href={item.source_url.startsWith("/")
                  ? `${process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000"}${item.source_url}`
                  : item.source_url}
                target="_blank"
                rel="noreferrer noopener"
                className="text-xs text-blue-600 dark:text-blue-400 hover:underline break-all"
              >
                {item.source_url}
              </a>
            )}
          </div>

          {/* 작업 액션 — is_read 토글 */}
          <div className="flex gap-2">
            <button
              type="button"
              onClick={toggleRead}
              disabled={saving}
              className={`flex-1 px-3 py-1.5 text-xs rounded transition ${
                item.is_read
                  ? "bg-zinc-200 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-400"
                  : "bg-yellow-100 dark:bg-yellow-900/30 text-yellow-800 dark:text-yellow-300 font-medium"
              } disabled:opacity-50`}
            >
              {item.is_read ? "✓ 읽음" : "● 안 읽음 (클릭 → 읽음)"}
            </button>
          </div>

          {/* 사용자 메모 — user_notes 편집 (PATCH BackgroundTask 로 LLM 키워드 자동) */}
          <section>
            <div className="text-[10px] uppercase tracking-wider text-zinc-500 mb-1 flex items-center justify-between">
              <span>내 메모 / 아이디어</span>
              {item.user_notes_updated_at && (
                <span className="normal-case text-zinc-400">
                  {new Date(item.user_notes_updated_at).toLocaleString("ko-KR")}
                </span>
              )}
            </div>
            <textarea
              value={notesDraft}
              onChange={(e) => setNotesDraft(e.target.value)}
              placeholder="이 자료에 대한 메모, 아이디어, 활용 방안 등을 자유롭게…"
              rows={4}
              className="w-full text-xs px-2 py-1.5 bg-zinc-50 dark:bg-zinc-800 border border-zinc-300 dark:border-zinc-700 rounded focus:outline-none focus:ring-1 focus:ring-orange-500 resize-y"
            />
            <button
              type="button"
              onClick={saveNotes}
              disabled={saving || notesDraft === (item.user_notes || "")}
              className="mt-1 px-3 py-1 text-xs bg-orange-500 hover:bg-orange-600 text-white rounded disabled:opacity-40"
            >
              저장 (LLM 키워드 자동 추출)
            </button>
          </section>

          {/* tags */}
          {item.tags.length > 0 && (
            <section>
              <div className="text-[10px] uppercase tracking-wider text-zinc-500 mb-1">
                Tags
              </div>
              <div className="flex flex-wrap gap-1">
                {item.tags.map((tag) => (
                  <span
                    key={tag}
                    className="text-[10px] px-1.5 py-0.5 bg-zinc-100 dark:bg-zinc-800 rounded"
                  >
                    {tag}
                  </span>
                ))}
              </div>
            </section>
          )}

          {/* summary */}
          {item.summary && (
            <section>
              <div className="text-[10px] uppercase tracking-wider text-zinc-500 mb-1">
                요약
              </div>
              <div className="text-xs text-zinc-700 dark:text-zinc-300 whitespace-pre-wrap leading-relaxed bg-zinc-50 dark:bg-zinc-800 p-2 rounded">
                {item.summary}
              </div>
            </section>
          )}

          {/* modality-aware viewer */}
          <ModalityViewer item={item} />

          {/* 첨부 (figures + PDF original 등) */}
          {item.attachments.length > 0 && (
            <section>
              <div className="text-[10px] uppercase tracking-wider text-zinc-500 mb-1">
                Attachments ({item.attachments.length})
              </div>
              <AttachmentList attachments={item.attachments} />
            </section>
          )}

          {/* raw_content — expandable */}
          <details>
            <summary className="text-[10px] uppercase tracking-wider text-zinc-500 cursor-pointer hover:text-zinc-700">
              raw content ({item.raw_content.length.toLocaleString()} chars)
            </summary>
            <pre className="mt-2 text-[10px] leading-relaxed whitespace-pre-wrap bg-zinc-50 dark:bg-zinc-800 p-2 rounded max-h-96 overflow-y-auto">
              {item.raw_content}
            </pre>
          </details>

          {/* metadata */}
          <section className="text-[10px] text-zinc-400 space-y-0.5 pt-2 border-t border-zinc-200 dark:border-zinc-800">
            <div>id: <code className="text-zinc-500">{item.id}</code></div>
            <div>ingested: {new Date(item.ingested_at).toLocaleString("ko-KR")}</div>
            {item.read_at && (
              <div>first read: {new Date(item.read_at).toLocaleString("ko-KR")}</div>
            )}
          </section>
        </div>
      )}
    </aside>
  );
}

// ──────────────────────────────────────────────────────────────
// ModalityViewer — source_type 별 modality 특화 view
// ──────────────────────────────────────────────────────────────

function ModalityViewer({ item }: { item: ItemDetail }) {
  const modality = modalityOf(item.source_type);

  // PDF 의 figures — attachments role='figure'
  const figures = item.attachments.filter((a) => a.role === "figure");

  if (modality === "pdf") {
    return (
      <section>
        <div className="text-[10px] uppercase tracking-wider text-zinc-500 mb-1">
          PDF
        </div>
        {item.source_url && (
          <a
            href={item.source_url.startsWith("/")
              ? `${process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000"}${item.source_url}`
              : item.source_url}
            target="_blank"
            rel="noreferrer noopener"
            className="block mb-2 text-xs text-blue-600 dark:text-blue-400 hover:underline"
          >
            📄 원본 PDF 열기
          </a>
        )}
        {figures.length > 0 && (
          <div>
            <div className="text-[10px] text-zinc-500 mb-1">
              Figures ({figures.length})
            </div>
            <div className="grid grid-cols-2 gap-1">
              {figures.map((fig) => (
                <a
                  key={fig.id}
                  href={fileUrl(fig.file_hash)}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="block group"
                  title={fig.caption || ""}
                >
                  <img
                    src={fileUrl(fig.file_hash)}
                    alt={fig.caption || "figure"}
                    className="w-full h-24 object-cover bg-zinc-100 dark:bg-zinc-800 rounded group-hover:ring-2 group-hover:ring-orange-500"
                  />
                  {fig.caption && (
                    <div className="text-[9px] text-zinc-500 truncate mt-0.5">
                      {fig.caption}
                    </div>
                  )}
                </a>
              ))}
            </div>
          </div>
        )}
      </section>
    );
  }

  if (modality === "youtube") {
    const thumbnail = item.attachments.find((a) => a.role === "thumbnail");
    return (
      <section>
        <div className="text-[10px] uppercase tracking-wider text-zinc-500 mb-1">
          YouTube {item.source_type === "youtube_playlist" && "(playlist)"}
        </div>
        {thumbnail && (
          <img
            src={fileUrl(thumbnail.file_hash)}
            alt="thumbnail"
            className="w-full rounded mb-2"
          />
        )}
        {item.source_url && (
          <a
            href={item.source_url}
            target="_blank"
            rel="noreferrer noopener"
            className="text-xs text-red-600 dark:text-red-400 hover:underline"
          >
            ▶ YouTube 열기
          </a>
        )}
      </section>
    );
  }

  if (modality === "github") {
    return (
      <section>
        <div className="text-[10px] uppercase tracking-wider text-zinc-500 mb-1">
          GitHub
        </div>
        {item.source_url && (
          <a
            href={item.source_url}
            target="_blank"
            rel="noreferrer noopener"
            className="text-xs text-purple-600 dark:text-purple-400 hover:underline"
          >
            🔗 repo 열기
          </a>
        )}
      </section>
    );
  }

  // url / document / telegram / other — 별도 특화 없이 기본
  return null;
}

// ──────────────────────────────────────────────────────────────
// AttachmentList
// ──────────────────────────────────────────────────────────────

function AttachmentList({ attachments }: { attachments: ItemAttachment[] }) {
  return (
    <ul className="space-y-1">
      {attachments.map((a) => (
        <li
          key={a.id}
          className="text-[11px] flex items-center justify-between gap-2 py-1 px-2 bg-zinc-50 dark:bg-zinc-800 rounded"
        >
          <span className="truncate flex-1" title={a.caption || ""}>
            <span className="text-zinc-500">{a.role || "file"}</span>
            {a.caption && (
              <span className="ml-1 text-zinc-700 dark:text-zinc-300">
                {a.caption}
              </span>
            )}
          </span>
          <span className="text-zinc-400 text-[10px] shrink-0">
            {a.mime_type}
          </span>
          <a
            href={fileUrl(a.file_hash)}
            target="_blank"
            rel="noreferrer noopener"
            className="text-blue-600 dark:text-blue-400 hover:underline shrink-0"
          >
            열기
          </a>
        </li>
      ))}
    </ul>
  );
}
