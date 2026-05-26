"use client";

/**
 * D10 wave-2d — wiki page 의 키워드 pill UI (2026-05-26 사용자 UX 개선).
 *
 * 두 모드:
 *  - view (기본): pill click → /wiki?keyword=X navigate (matching wiki list). 깔끔.
 *  - edit: [✏️ 수정] 클릭 시 진입. pill 옆 ✕ + [+ 추가] input + autocomplete dropdown.
 *          [완료] 클릭 시 종료.
 *
 * autocomplete:
 *  - DB 의 모든 wiki_pages 의 keywords UNNEST + 사용 빈도 정렬
 *  - 입력 keyword 가 DB 에 없으면 "✨ 신규 등록" hint
 */

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import {
  searchWikiKeywords,
  updateWikiKeywords,
  type WikiKeywordSuggestion,
} from "@/lib/api";

interface Props {
  slug: string;
  keywords: string[];
  onChange: (next: string[]) => void;
}

export default function KeywordsEditor({ slug, keywords, onChange }: Props) {
  const [editMode, setEditMode] = useState(false);
  const [showInput, setShowInput] = useState(false);
  const [input, setInput] = useState("");
  const [suggestions, setSuggestions] = useState<WikiKeywordSuggestion[]>([]);
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  // autocomplete debounce
  useEffect(() => {
    if (!showInput) return;
    const id = setTimeout(async () => {
      try {
        const r = await searchWikiKeywords(input || "", 15);
        const lower = new Set(keywords.map((k) => k.toLowerCase()));
        setSuggestions(r.suggestions.filter((s) => !lower.has(s.keyword.toLowerCase())));
      } catch {
        setSuggestions([]);
      }
    }, 200);
    return () => clearTimeout(id);
  }, [input, showInput, keywords]);

  useEffect(() => {
    if (showInput) inputRef.current?.focus();
  }, [showInput]);

  const handleRemove = async (kw: string) => {
    setBusy(true);
    try {
      const r = await updateWikiKeywords(slug, { remove: [kw] });
      onChange(r.keywords);
    } finally {
      setBusy(false);
    }
  };

  const handleAdd = async (kw: string) => {
    const trimmed = kw.trim();
    if (!trimmed) return;
    setBusy(true);
    try {
      const r = await updateWikiKeywords(slug, { add: [trimmed] });
      onChange(r.keywords);
      setInput("");
      inputRef.current?.focus();
    } finally {
      setBusy(false);
    }
  };

  const handleKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      handleAdd(input);
    } else if (e.key === "Escape") {
      setShowInput(false);
      setInput("");
    }
  };

  const inputTrim = input.trim();
  const exactMatch =
    !!inputTrim &&
    (suggestions.some((s) => s.keyword.toLowerCase() === inputTrim.toLowerCase()) ||
      keywords.some((k) => k.toLowerCase() === inputTrim.toLowerCase()));

  const exitEdit = () => {
    setEditMode(false);
    setShowInput(false);
    setInput("");
  };

  return (
    <section className="bg-white dark:bg-zinc-900 rounded border border-zinc-200 dark:border-zinc-800 p-4">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
          Keywords ({keywords.length})
        </h3>
        {editMode ? (
          <button
            type="button"
            onClick={exitEdit}
            disabled={busy}
            className="text-xs px-2 py-0.5 rounded border border-emerald-300 dark:border-emerald-700 text-emerald-700 dark:text-emerald-400 hover:bg-emerald-50 dark:hover:bg-emerald-900/20"
          >
            ✓ 완료
          </button>
        ) : (
          <button
            type="button"
            onClick={() => setEditMode(true)}
            className="text-xs px-2 py-0.5 rounded border border-zinc-300 dark:border-zinc-700 text-zinc-600 dark:text-zinc-400 hover:bg-zinc-50 dark:hover:bg-zinc-800"
          >
            ✏️ 수정
          </button>
        )}
      </div>

      {/* 현재 키워드 pill list */}
      <div className="flex flex-wrap gap-1.5 mb-2">
        {keywords.length === 0 && (
          <span className="text-[10px] text-zinc-500 dark:text-zinc-400 italic">
            (키워드 없음 — [수정] 으로 추가하거나 wiki 재합성 시 자동 추출)
          </span>
        )}
        {keywords.map((kw) =>
          editMode ? (
            // ── edit mode: ✕ 보임, click 안 됨
            <span
              key={kw}
              className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full bg-amber-50 dark:bg-amber-900/20 text-amber-800 dark:text-amber-300 border border-amber-300 dark:border-amber-700"
            >
              <span>{kw}</span>
              <button
                type="button"
                onClick={() => handleRemove(kw)}
                disabled={busy}
                className="text-amber-500 hover:text-red-500 text-xs leading-none"
                title="삭제"
              >
                ×
              </button>
            </span>
          ) : (
            // ── view mode: click → matching wiki list
            <Link
              key={kw}
              href={`/wiki?keyword=${encodeURIComponent(kw)}`}
              className="text-[11px] px-2 py-0.5 rounded-full bg-zinc-100 dark:bg-zinc-800 text-zinc-700 dark:text-zinc-300 border border-zinc-200 dark:border-zinc-700 hover:bg-orange-50 dark:hover:bg-orange-900/20 hover:border-orange-300 hover:text-orange-700 dark:hover:text-orange-400 transition"
              title={`'${kw}' 키워드 가진 모든 wiki`}
            >
              {kw}
            </Link>
          ),
        )}
      </div>

      {/* edit mode: + 추가 버튼 / input */}
      {editMode && (
        <div className="mt-3 pt-3 border-t border-zinc-100 dark:border-zinc-800">
          {!showInput ? (
            <button
              type="button"
              onClick={() => setShowInput(true)}
              disabled={busy}
              className="text-xs px-3 py-1 rounded border border-orange-300 dark:border-orange-700 text-orange-700 dark:text-orange-400 hover:bg-orange-50 dark:hover:bg-orange-900/20"
            >
              + 키워드 추가
            </button>
          ) : (
            <div className="relative">
              <div className="flex gap-1">
                <input
                  ref={inputRef}
                  type="text"
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={handleKey}
                  placeholder="키워드 입력 (Enter 추가, Esc 취소)"
                  className="flex-1 text-xs px-2 py-1 rounded border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 text-zinc-900 dark:text-zinc-100"
                />
                <button
                  type="button"
                  onClick={() => {
                    setShowInput(false);
                    setInput("");
                  }}
                  className="text-xs px-2 py-1 text-zinc-500 hover:text-zinc-700"
                >
                  취소
                </button>
              </div>

              {(suggestions.length > 0 || inputTrim) && (
                <div className="mt-1 max-h-48 overflow-y-auto rounded border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 shadow text-xs z-10 relative">
                  {inputTrim && !exactMatch && (
                    <button
                      type="button"
                      onClick={() => handleAdd(inputTrim)}
                      disabled={busy}
                      className="w-full text-left px-2 py-1.5 hover:bg-orange-50 dark:hover:bg-orange-900/20 border-b border-zinc-100 dark:border-zinc-800 text-orange-700 dark:text-orange-400"
                    >
                      ✨ &quot;{inputTrim}&quot; 신규 등록
                    </button>
                  )}
                  {suggestions.map((s) => (
                    <button
                      key={s.keyword}
                      type="button"
                      onClick={() => handleAdd(s.keyword)}
                      disabled={busy}
                      className="w-full text-left px-2 py-1 hover:bg-zinc-100 dark:hover:bg-zinc-800 flex items-center justify-between"
                    >
                      <span className="text-zinc-700 dark:text-zinc-300">{s.keyword}</span>
                      <span className="text-[10px] text-zinc-400">
                        {s.usage_count} pages
                      </span>
                    </button>
                  ))}
                  {suggestions.length === 0 && !inputTrim && (
                    <div className="px-2 py-1.5 text-zinc-500 italic">
                      (DB 에 매칭 키워드 없음 — 위에 입력하면 신규 등록)
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          <p className="mt-2 text-[10px] text-zinc-500 dark:text-zinc-400">
            💡 view 모드 (수정 종료 후) 에서 키워드 클릭 → 같은 키워드 가진 wiki 들 list
          </p>
        </div>
      )}
    </section>
  );
}
