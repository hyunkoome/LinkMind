"use client";

/**
 * /ask — D11 대화형 RAG (2026-05-27).
 *
 * VSCode 스타일 multi-panel:
 *  - 좌측 (lg:w-[28rem]): 채팅 (질문 + LLM 답변 + citation + related wikis)
 *  - 우측 (flex-1): 선택한 wiki detail (사이드 inline view)
 *
 * 핵심 비전 (CLAUDE.md §1):
 *  - 자체 DB (wiki + items) + 로컬 LLM (vLLM Qwen2.5-7B) 기반 답변
 *  - 미래 sVLL LoRA 파인튜닝 결과가 자동 반영 (provider swap 한 곳)
 *  - 그래프는 Step 2 에서 우측 panel 추가 (지금은 2-column)
 */

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import ModelLabel from "@/components/ModelLabel";
import KeywordsEditor from "@/components/wiki/KeywordsEditor";
import WikiBody from "@/components/wiki/WikiBody";
import {
  askQuestion,
  getWikiPage,
  type AskCitation,
  type AskRelatedWiki,
  type AskResponse,
  type WikiPageDetail,
} from "@/lib/api";

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  ts: string;
  citations?: AskCitation[];
  related_wikis?: AskRelatedWiki[];
  llm_model?: string;
}

// 2026-05-27 rename: 'issues' / 'pending' / 'completed' (wiki list 와 일관).
const STATUS_COLORS: Record<string, string> = {
  completed: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400",
  pending: "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400 animate-pulse",
  issues: "bg-rose-100 text-rose-700 dark:bg-rose-900/30 dark:text-rose-400",
};

export default function AskPage() {
  const [input, setInput] = useState("");
  const [pending, setPending] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [error, setError] = useState<string | null>(null);

  // 우측 panel — 선택한 wiki
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null);
  const [wikiPage, setWikiPage] = useState<WikiPageDetail | null>(null);
  const [wikiLoading, setWikiLoading] = useState(false);

  // 채팅 history auto-scroll
  const chatEndRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, pending]);

  // selectedSlug 변경 → wiki detail fetch
  useEffect(() => {
    if (!selectedSlug) {
      setWikiPage(null);
      return;
    }
    setWikiLoading(true);
    getWikiPage(selectedSlug)
      .then((p) => setWikiPage(p))
      .catch((e) => setError((e as Error).message))
      .finally(() => setWikiLoading(false));
  }, [selectedSlug]);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const q = input.trim();
    if (!q || pending) return;

    const userMsg: ChatMessage = {
      role: "user",
      content: q,
      ts: new Date().toLocaleTimeString("ko-KR"),
    };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setPending(true);
    setError(null);

    try {
      const r: AskResponse = await askQuestion({ question: q, top_k: 5 });
      const assistantMsg: ChatMessage = {
        role: "assistant",
        content: r.answer,
        ts: new Date().toLocaleTimeString("ko-KR"),
        citations: r.citations,
        related_wikis: r.related_wikis,
        llm_model: `${r.llm_provider}/${r.llm_model}`,
      };
      setMessages((prev) => [...prev, assistantMsg]);
      // 자동으로 가장 관련도 높은 wiki 우측 panel 에 표시
      if (r.related_wikis.length > 0 && !selectedSlug) {
        setSelectedSlug(r.related_wikis[0].slug);
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="flex-1 flex overflow-hidden bg-zinc-50 dark:bg-zinc-950">
      {/* 좌측: 채팅 */}
      <section className="w-full lg:w-[28rem] flex flex-col border-r border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900">
        <header className="shrink-0 px-4 py-3 border-b border-zinc-200 dark:border-zinc-800">
          <h1 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
            🤖 LinkMind Ask
          </h1>
          <p className="text-[11px] text-zinc-500 dark:text-zinc-400 mt-0.5">
            자체 DB + 로컬 LLM (vLLM <ModelLabel />) 기반 답변. citation 클릭하면 우측에 wiki 표시.
          </p>
        </header>

        {/* 채팅 history */}
        <div className="flex-1 overflow-y-auto p-3 space-y-3 min-h-0">
          {messages.length === 0 && (
            <div className="text-center py-12 text-xs text-zinc-500">
              <div className="text-2xl mb-2">💭</div>
              질문을 입력하면 LinkMind 가 자체 DB 의 자료로 답합니다.
              <div className="mt-3 text-[10px] text-zinc-400">
                예: &quot;LoRA 가 뭐야?&quot; · &quot;factor graph 어떻게 동작해?&quot;
              </div>
            </div>
          )}

          {messages.map((m, i) => (
            <article
              key={i}
              className={`text-xs rounded p-2.5 ${
                m.role === "user"
                  ? "bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800"
                  : "bg-zinc-50 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700"
              }`}
            >
              <div className="flex items-center justify-between mb-1">
                <span className="font-medium text-zinc-700 dark:text-zinc-300">
                  {m.role === "user" ? "👤 나" : "🤖 LinkMind"}
                </span>
                <span className="text-[10px] text-zinc-400">{m.ts}</span>
              </div>

              <div className="whitespace-pre-wrap text-zinc-800 dark:text-zinc-200 leading-relaxed">
                {m.content}
              </div>

              {/* citations (assistant 만) */}
              {m.citations && m.citations.length > 0 && (
                <details className="mt-2 text-[10px]">
                  <summary className="cursor-pointer text-zinc-500 hover:text-zinc-700">
                    📎 인용 {m.citations.length}건
                  </summary>
                  <ul className="mt-1 space-y-0.5 pl-3">
                    {m.citations.map((c, j) => (
                      <li key={c.item_id} className="text-zinc-600 dark:text-zinc-400">
                        <span className="text-zinc-400 mr-1">[{j + 1}]</span>
                        {c.source_url ? (
                          <a
                            href={c.source_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-blue-600 dark:text-blue-400 hover:underline"
                          >
                            {c.title || c.item_id.slice(0, 8)}
                          </a>
                        ) : (
                          <span>{c.title || c.item_id.slice(0, 8)}</span>
                        )}
                      </li>
                    ))}
                  </ul>
                </details>
              )}

              {/* related wikis — 클릭하면 우측에 표시 */}
              {m.related_wikis && m.related_wikis.length > 0 && (
                <div className="mt-2 pt-2 border-t border-zinc-200 dark:border-zinc-700">
                  <div className="text-[10px] text-zinc-500 mb-1">
                    📖 관련 위키 {m.related_wikis.length}건
                  </div>
                  <ul className="space-y-0.5">
                    {m.related_wikis.map((w) => (
                      <li key={w.slug}>
                        <button
                          type="button"
                          onClick={() => setSelectedSlug(w.slug)}
                          className={`w-full text-left text-[11px] px-1.5 py-1 rounded hover:bg-zinc-100 dark:hover:bg-zinc-700 ${
                            selectedSlug === w.slug
                              ? "bg-orange-100 dark:bg-orange-900/30 text-orange-700 dark:text-orange-300"
                              : "text-zinc-700 dark:text-zinc-300"
                          }`}
                        >
                          <span className="font-medium truncate inline-block max-w-[280px] align-middle">
                            {w.title}
                          </span>
                          <span className="ml-1 text-zinc-400 text-[10px]">
                            ({w.overlap})
                          </span>
                          <span
                            className={`ml-1 inline-block text-[9px] px-1 rounded ${STATUS_COLORS[w.body_status] || STATUS_COLORS.issues}`}
                          >
                            {w.body_status}
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {m.llm_model && (
                <div className="mt-1.5 text-[9px] text-zinc-400 font-mono">
                  {m.llm_model}
                </div>
              )}
            </article>
          ))}

          {pending && (
            <div className="text-xs text-zinc-500 italic">
              🤖 LinkMind 가 자체 DB 검색 중… (vLLM ~30-60초)
            </div>
          )}

          {error && (
            <div className="text-xs text-red-700 dark:text-red-300 bg-red-50 dark:bg-red-900/20 p-2 rounded border border-red-200 dark:border-red-800">
              에러: {error}
            </div>
          )}

          <div ref={chatEndRef} />
        </div>

        {/* 입력 */}
        <form
          onSubmit={onSubmit}
          className="shrink-0 p-3 border-t border-zinc-200 dark:border-zinc-800"
        >
          <div className="flex gap-2">
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="자료에 대해 자연어로 질문…"
              disabled={pending}
              className="flex-1 px-3 py-1.5 text-sm rounded border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-950 text-zinc-900 dark:text-zinc-100 focus:outline-none focus:border-orange-500 disabled:opacity-50"
            />
            <button
              type="submit"
              disabled={pending || !input.trim()}
              className="px-3 py-1.5 text-sm rounded bg-orange-500 hover:bg-orange-600 text-white font-medium disabled:opacity-50"
            >
              {pending ? "…" : "전송"}
            </button>
          </div>
        </form>
      </section>

      {/* 우측: 선택한 wiki detail */}
      <section className="hidden lg:flex flex-1 flex-col overflow-hidden">
        {!selectedSlug ? (
          <div className="flex-1 flex items-center justify-center text-zinc-400 dark:text-zinc-600">
            <div className="text-center">
              <div className="text-5xl mb-3">📖</div>
              <div className="text-sm">
                답변의 관련 위키를 클릭하면 여기에 표시됩니다
              </div>
            </div>
          </div>
        ) : wikiLoading || !wikiPage ? (
          <div className="flex-1 flex items-center justify-center text-zinc-500 text-sm">
            wiki 로딩 중…
          </div>
        ) : (
          <div className="flex-1 overflow-y-auto p-6">
            <div className="max-w-3xl mx-auto">
              <div className="flex items-center justify-between mb-3">
                <Link
                  href={`/wiki/${encodeURIComponent(wikiPage.slug)}`}
                  className="text-xs text-orange-600 dark:text-orange-400 hover:underline"
                >
                  /wiki/{wikiPage.slug} ↗
                </Link>
                <button
                  type="button"
                  onClick={() => setSelectedSlug(null)}
                  className="text-xs text-zinc-500 hover:text-zinc-700"
                >
                  ✕ 닫기
                </button>
              </div>

              <article className="bg-white dark:bg-zinc-900 rounded border border-zinc-200 dark:border-zinc-800 p-6">
                <WikiBody body={wikiPage.body || ""} />
              </article>

              {/* aside compact — Sources + Keywords */}
              <div className="mt-4 grid grid-cols-1 md:grid-cols-2 gap-3">
                <section className="bg-white dark:bg-zinc-900 rounded border border-zinc-200 dark:border-zinc-800 p-3">
                  <h3 className="text-xs font-semibold mb-1.5">
                    Sources ({wikiPage.sources.length})
                  </h3>
                  <ul className="space-y-1">
                    {wikiPage.sources.slice(0, 8).map((s, i) => (
                      <li key={s.item_id} className="text-[11px] text-zinc-700 dark:text-zinc-300">
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
                        <span className="ml-1 text-[10px] text-zinc-400">
                          {s.source_type}
                        </span>
                      </li>
                    ))}
                  </ul>
                </section>

                <KeywordsEditor
                  slug={wikiPage.slug}
                  keywords={wikiPage.keywords || []}
                  onChange={(next) => setWikiPage({ ...wikiPage, keywords: next })}
                />
              </div>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
