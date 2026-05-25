"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  API_BASE,
  appendItemNote,
  deleteItem,
  getItem,
  linkItemCategory,
  listCategories,
  type CategorySummary,
  type ItemListCard,
} from "@/lib/api";
import { useT } from "@/lib/i18n/context";
import type { ItemDetail } from "@/types/graph";

interface Props {
  card: ItemListCard | null;
  onUpdated: (id: string) => void;   // detail 갱신 후 list refresh 신호
  onDeleted?: (id: string) => void;  // 삭제 완료 후 list refresh + 패널 닫기
  onClose: () => void;
}

export default function ActionPanel({ card, onUpdated, onDeleted, onClose }: Props) {
  const { locale, t } = useT();
  const [detail, setDetail] = useState<ItemDetail | null>(null);
  const [categories, setCategories] = useState<CategorySummary[]>([]);
  const [noteDraft, setNoteDraft] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [categoryQuery, setCategoryQuery] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);

  useEffect(() => {
    if (!card) {
      setDetail(null);
      setNoteDraft("");
      setStatus(null);
      setError(null);
      setConfirmDelete(false);
      return;
    }
    setConfirmDelete(false);  // 카드 바뀌면 confirm 상태 reset
    let alive = true;
    getItem(card.id)
      .then((d) => {
        if (alive) setDetail(d);
      })
      .catch((e) => {
        if (alive) setError((e as Error).message);
      });
    return () => { alive = false; };
  }, [card]);

  useEffect(() => {
    let alive = true;
    // categories endpoint 가 일부 row 의 synonyms NULL 로 throw 하는 별개 버그가
    // 있을 수 있어 graceful fallback (빈 list) — manual category link 액션이
    // 카테고리 dropdown 없이도 slug 직접 입력으로 동작하도록.
    listCategories(500)
      .then((c) => { if (alive) setCategories(c); })
      .catch(() => { if (alive) setCategories([]); });
    return () => { alive = false; };
  }, []);

  const filteredCategories = useMemo(() => {
    const q = categoryQuery.trim().toLowerCase();
    const list = q
      ? categories.filter(
        (c) =>
          c.slug.toLowerCase().includes(q) ||
          c.label.toLowerCase().includes(q),
      )
      : categories;
    return list.slice(0, 30);
  }, [categories, categoryQuery]);

  const submitNote = useCallback(async () => {
    if (!card || !noteDraft.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      const updated = await appendItemNote(card.id, noteDraft.trim());
      setDetail(updated);
      setNoteDraft("");
      setStatus(locale === "ko" ? "메모가 저장됐습니다 (LLM 태그 추출 중…)" : "Note saved (LLM tagging in progress…)");
      onUpdated(card.id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSubmitting(false);
    }
  }, [card, noteDraft, locale, onUpdated]);

  const linkCategory = useCallback(async (slug: string) => {
    if (!card) return;
    setSubmitting(true);
    setError(null);
    try {
      const res = await linkItemCategory(card.id, slug);
      setStatus(
        locale === "ko"
          ? `카테고리 "${slug}" 연결 완료 (topic: ${res.topic_slug})`
          : `Linked to category "${slug}" (topic: ${res.topic_slug})`,
      );
      onUpdated(card.id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSubmitting(false);
    }
  }, [card, locale, onUpdated]);

  const confirmDeleteAction = useCallback(async () => {
    if (!card) return;
    setSubmitting(true);
    setError(null);
    try {
      await deleteItem(card.id);
      // 삭제 성공 — 상위에 알리고 패널 닫기
      onDeleted?.(card.id);
    } catch (e) {
      setError((e as Error).message);
      setConfirmDelete(false);
    } finally {
      setSubmitting(false);
    }
  }, [card, onDeleted]);

  if (!card) {
    return (
      <aside className="w-96 shrink-0 h-full overflow-y-auto border-l border-zinc-200 dark:border-zinc-800 bg-zinc-50 dark:bg-zinc-950 p-4 flex items-center justify-center text-xs text-zinc-500">
        {locale === "ko"
          ? "왼쪽에서 자료를 선택하면 액션 패널이 열립니다"
          : "Select an item to open action panel"}
      </aside>
    );
  }

  const url = card.source_url
    ? card.source_url.startsWith("/")
      ? `${API_BASE}${card.source_url}`
      : card.source_url
    : null;

  return (
    <aside className="w-96 shrink-0 h-full overflow-y-auto border-l border-zinc-200 dark:border-zinc-800 bg-zinc-50 dark:bg-zinc-950 p-4">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold">
          {locale === "ko" ? "수동 액션" : "Manual actions"}
        </h2>
        <button
          type="button"
          onClick={onClose}
          className="text-xs text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100"
          aria-label={t.common.close}
        >
          ✕
        </button>
      </div>

      <h3 className="text-sm font-medium mb-2 break-words">
        {card.title || "(no title)"}
      </h3>

      {url && (
        <a
          href={url}
          target="_blank"
          rel="noreferrer noopener"
          className="block text-[11px] text-blue-600 dark:text-blue-400 hover:underline break-all mb-3"
        >
          🔗 {locale === "ko" ? "원본 새 창 열기" : "Open original"} → {url}
        </a>
      )}

      {/* kind 별 hint — 사용자가 어떤 액션을 취해야 하는지 안내 */}
      {card.fetch_error_kind === "image_no_ocr" && (
        <div className="mb-3 p-2 text-[11px] bg-amber-50 dark:bg-amber-900/20 text-amber-800 dark:text-amber-200 rounded">
          🖼 {locale === "ko"
            ? "이미지 자료 — Phase 3 OCR 도입 전까지는 사용자가 이미지 내용을 메모로 직접 입력해주세요."
            : "Image asset — until Phase 3 OCR, manually describe the image content in notes."}
        </div>
      )}
      {card.fetch_error_kind === "extraction_failed" && (
        <div className="mb-3 p-2 text-[11px] bg-red-50 dark:bg-red-900/20 text-red-800 dark:text-red-200 rounded">
          ⚠ {locale === "ko"
            ? "본문 자동 추출 실패 — 원본을 열어 핵심 내용을 복사해 메모에 붙여넣거나 archive.org 미러를 시도하세요."
            : "Extraction failed — open the original, copy key content to notes, or try archive.org."}
        </div>
      )}
      {card.fetch_error_kind === "short_raw" && card.source_type === "pdf" && (
        <div className="mb-3 p-2 text-[11px] bg-orange-50 dark:bg-orange-900/20 text-orange-800 dark:text-orange-200 rounded">
          📄 {locale === "ko"
            ? "PDF 본문 추출이 거의 빈 결과 — abstract 또는 핵심 본문을 직접 붙여넣어주세요."
            : "PDF body extraction near-empty — paste abstract or key body manually."}
        </div>
      )}
      {card.fetch_error_kind === "short_raw" && card.source_type !== "pdf" && (
        <div className="mb-3 p-2 text-[11px] bg-orange-50 dark:bg-orange-900/20 text-orange-800 dark:text-orange-200 rounded">
          📝 {locale === "ko"
            ? "본문이 거의 비어있습니다 — 원본 페이지에서 내용을 복사해 메모로 보강해주세요."
            : "Body nearly empty — copy content from source into notes."}
        </div>
      )}
      {card.fetch_error_kind === "binary_no_extract" && (
        <div className="mb-3 p-2 text-[11px] bg-zinc-100 dark:bg-zinc-800 text-zinc-700 dark:text-zinc-300 rounded">
          📦 {locale === "ko"
            ? "텍스트 추출 불가 바이너리 — 파일 설명을 메모로 입력하거나 카테고리만 지정해주세요."
            : "Non-text binary — add description in notes or just assign a category."}
        </div>
      )}

      {/* 상태 + 에러 */}
      {status && (
        <div className="mb-3 p-2 text-[11px] bg-green-50 dark:bg-green-900/20 text-green-700 dark:text-green-300 rounded">
          ✓ {status}
        </div>
      )}
      {error && (
        <div className="mb-3 p-2 text-[11px] bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-300 rounded">
          ⚠ {error}
        </div>
      )}

      {/* user_notes append */}
      <section className="mb-5">
        <label className="block text-[11px] font-medium text-zinc-700 dark:text-zinc-300 mb-1">
          {locale === "ko"
            ? "📝 본문/메모 추가 (user_notes 에 append, 덮어쓰기 X)"
            : "📝 Add note / paste content (appends to user_notes)"}
        </label>
        <textarea
          value={noteDraft}
          onChange={(e) => setNoteDraft(e.target.value)}
          placeholder={
            locale === "ko"
              ? "원본 본문을 수동으로 붙여넣거나 본인 메모를 입력…"
              : "Paste content from source or write a note…"
          }
          rows={8}
          className="w-full px-2 py-1.5 text-xs bg-white dark:bg-zinc-900 border border-zinc-300 dark:border-zinc-700 rounded focus:outline-none focus:ring-1 focus:ring-orange-500 font-mono"
        />
        <button
          type="button"
          onClick={submitNote}
          disabled={submitting || !noteDraft.trim()}
          className="mt-1.5 w-full px-3 py-1.5 text-xs bg-orange-500 hover:bg-orange-600 text-white rounded disabled:opacity-50"
        >
          {submitting
            ? t.common.saving
            : locale === "ko" ? "메모 추가" : "Append note"}
        </button>
      </section>

      {/* 기존 메모 표시 */}
      {detail?.user_notes && (
        <section className="mb-5">
          <div className="text-[11px] font-medium text-zinc-500 mb-1">
            {locale === "ko" ? "기존 메모" : "Existing notes"}
          </div>
          <pre className="text-[11px] text-cyan-700 dark:text-cyan-300 whitespace-pre-wrap font-mono bg-cyan-50 dark:bg-cyan-900/20 p-2 rounded max-h-48 overflow-y-auto">
            {detail.user_notes}
          </pre>
        </section>
      )}

      {/* 카테고리 link */}
      <section className="mb-5">
        <label className="block text-[11px] font-medium text-zinc-700 dark:text-zinc-300 mb-1">
          {locale === "ko"
            ? "🏷 카테고리 수동 지정 (topic 단위로 연결)"
            : "🏷 Link to category (topic-level)"}
        </label>
        <input
          type="text"
          value={categoryQuery}
          onChange={(e) => setCategoryQuery(e.target.value)}
          placeholder={locale === "ko" ? "검색 또는 slug 직접 입력…" : "Search or type slug…"}
          className="w-full px-2 py-1.5 text-xs bg-white dark:bg-zinc-900 border border-zinc-300 dark:border-zinc-700 rounded focus:outline-none focus:ring-1 focus:ring-orange-500 mb-1.5"
        />
        {categoryQuery.trim() && !filteredCategories.find((c) => c.slug === categoryQuery.trim()) && (
          <button
            type="button"
            onClick={() => linkCategory(categoryQuery.trim())}
            disabled={submitting}
            className="w-full mb-1.5 px-2 py-1 text-[11px] bg-zinc-100 dark:bg-zinc-800 hover:bg-orange-100 dark:hover:bg-orange-900/30 rounded text-left"
          >
            {locale === "ko"
              ? `직접 입력한 slug "${categoryQuery.trim()}" 으로 연결`
              : `Link as slug "${categoryQuery.trim()}"`}
          </button>
        )}
        <ul className="space-y-0.5 max-h-48 overflow-y-auto">
          {filteredCategories.map((c) => (
            <li key={c.slug}>
              <button
                type="button"
                onClick={() => linkCategory(c.slug)}
                disabled={submitting}
                className="w-full text-left px-2 py-1 text-[11px] hover:bg-orange-100 dark:hover:bg-orange-900/30 rounded flex items-center justify-between"
              >
                <span className="truncate">
                  {c.label}
                  <span className="text-zinc-400 ml-1.5 font-mono">{c.slug}</span>
                </span>
                <span className="font-mono text-[9px] text-zinc-500 shrink-0">
                  {c.topic_count}
                </span>
              </button>
            </li>
          ))}
          {filteredCategories.length === 0 && categoryQuery.trim() === "" && (
            <li className="text-[10px] text-zinc-500 italic px-2 py-1">
              {locale === "ko"
                ? "카테고리 목록 로딩 중… 또는 slug 를 직접 입력하세요."
                : "Loading categories… or type a slug directly."}
            </li>
          )}
        </ul>
      </section>

      {/* raw_content preview (truncated) */}
      {detail && detail.raw_content && (
        <section className="mb-5">
          <div className="text-[11px] font-medium text-zinc-500 mb-1">
            {locale === "ko" ? "raw_content (첫 800자)" : "raw_content (first 800 chars)"}
          </div>
          <pre className="text-[10px] text-zinc-600 dark:text-zinc-400 whitespace-pre-wrap font-mono bg-zinc-100 dark:bg-zinc-900 p-2 rounded max-h-48 overflow-y-auto">
            {detail.raw_content.slice(0, 800)}
            {detail.raw_content.length > 800 ? "…" : ""}
          </pre>
        </section>
      )}

      {/* 영구 삭제 — 진짜 사라진 자료 (영상 삭제 / 도메인 죽음) 정리용.
          2단계 confirm — 첫 클릭은 경고 박스, 두 번째 클릭으로 확정. */}
      <section className="mt-6 pt-4 border-t border-zinc-300 dark:border-zinc-700">
        <div className="text-[11px] font-medium text-zinc-500 mb-1">
          {locale === "ko" ? "⚠ 영구 삭제 (irreversible)" : "⚠ Permanent delete"}
        </div>
        {!confirmDelete ? (
          <button
            type="button"
            onClick={() => setConfirmDelete(true)}
            disabled={submitting}
            className="w-full px-3 py-1.5 text-xs bg-white dark:bg-zinc-900 border border-red-300 dark:border-red-800 text-red-700 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20 rounded disabled:opacity-50"
          >
            🗑 {locale === "ko" ? "이 자료 삭제..." : "Delete this item..."}
          </button>
        ) : (
          <div className="p-3 text-[11px] bg-red-50 dark:bg-red-900/20 border border-red-300 dark:border-red-800 rounded">
            <div className="font-medium text-red-800 dark:text-red-200 mb-2">
              {locale === "ko"
                ? "정말 삭제하시겠습니까? 이 자료는 영구히 삭제됩니다."
                : "Really delete? This is irreversible."}
            </div>
            <div className="mb-2 text-zinc-700 dark:text-zinc-300">
              <div className="font-medium truncate" title={card.title || ""}>
                {card.title || "(no title)"}
              </div>
              {url && (
                <a
                  href={url}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="block text-[10px] text-blue-600 dark:text-blue-400 hover:underline break-all"
                  onClick={(e) => e.stopPropagation()}
                >
                  {url}
                </a>
              )}
              {detail?.raw_content && (
                <pre className="mt-1 text-[9px] text-zinc-500 dark:text-zinc-400 whitespace-pre-wrap font-mono bg-white dark:bg-zinc-900 p-1.5 rounded max-h-20 overflow-y-auto">
                  {detail.raw_content.slice(0, 200)}
                  {detail.raw_content.length > 200 ? "…" : ""}
                </pre>
              )}
            </div>
            <div className="flex gap-1.5">
              <button
                type="button"
                onClick={() => setConfirmDelete(false)}
                disabled={submitting}
                className="flex-1 px-2 py-1 text-[11px] bg-white dark:bg-zinc-900 border border-zinc-300 dark:border-zinc-700 rounded hover:bg-zinc-100 dark:hover:bg-zinc-800"
              >
                {t.common.cancel}
              </button>
              <button
                type="button"
                onClick={confirmDeleteAction}
                disabled={submitting}
                className="flex-1 px-2 py-1 text-[11px] bg-red-600 hover:bg-red-700 text-white rounded disabled:opacity-50 font-medium"
              >
                {submitting
                  ? t.common.saving
                  : locale === "ko" ? "삭제 확정" : "Confirm delete"}
              </button>
            </div>
          </div>
        )}
      </section>
    </aside>
  );
}
