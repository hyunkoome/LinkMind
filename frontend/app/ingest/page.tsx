"use client";

import { useState } from "react";

import { ingestAuto, uploadPdf } from "@/lib/api";
import { useT } from "@/lib/i18n/context";
import type { UrlIngestResponse } from "@/types/graph";

type LogEntry = {
  ts: string;
  kind: "ok" | "err";
  msg: string;
  detail?: string;
};

export default function IngestPage() {
  const { t, locale } = useT();
  const [url, setUrl] = useState("");
  const [force, setForce] = useState(false);
  const [pdfFile, setPdfFile] = useState<File | null>(null);
  const [pending, setPending] = useState(false);
  const [log, setLog] = useState<LogEntry[]>([]);
  const localeForDate = locale === "ko" ? "ko-KR" : "en-US";

  const appendLog = (entry: Omit<LogEntry, "ts">) => {
    setLog((prev) => [
      { ...entry, ts: new Date().toLocaleTimeString(localeForDate) },
      ...prev,
    ].slice(0, 30));
  };

  const submitUrl = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!url.trim() || pending) return;
    setPending(true);
    try {
      const r: UrlIngestResponse = await ingestAuto({
        url: url.trim(),
        force,
        analyze_now: true,
      });
      const label = r.created
        ? t.ingest.result.new
        : r.refreshed
          ? t.ingest.result.refreshed
          : t.ingest.result.duplicate;
      appendLog({
        kind: "ok",
        msg: `${label} · ${r.title || url}`,
        detail: `item_id=${r.item_id} chunks=${r.chunks_indexed ?? 0} figures=${r.figures_saved ?? 0} tags=${(r.tags || []).join(",")}`,
      });
      setUrl("");
    } catch (e) {
      appendLog({ kind: "err", msg: `${t.ingest.urlSectionTitle} ${t.common.error}: ${(e as Error).message}` });
    } finally {
      setPending(false);
    }
  };

  const submitPdf = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!pdfFile || pending) return;
    setPending(true);
    try {
      const r = await uploadPdf(pdfFile, force);
      appendLog({
        kind: "ok",
        msg: `${t.ingest.pdfSectionTitle} · ${r.title || pdfFile.name}`,
        detail: `item_id=${r.item_id} chunks=${r.chunks_indexed ?? 0} figures=${r.figures_saved ?? 0}`,
      });
      setPdfFile(null);
      // 파일 input 초기화
      (document.getElementById("pdf-input") as HTMLInputElement | null)?.value &&
        ((document.getElementById("pdf-input") as HTMLInputElement).value = "");
    } catch (e) {
      appendLog({ kind: "err", msg: `${t.ingest.pdfSectionTitle} ${t.common.error}: ${(e as Error).message}` });
    } finally {
      setPending(false);
    }
  };

  return (
    <main className="h-full overflow-y-auto p-6 max-w-3xl mx-auto w-full">
      <h1 className="text-xl font-semibold mb-1">{t.ingest.pageTitle}</h1>
      <p className="text-sm text-zinc-500 mb-6">{t.ingest.pageSubtitle}</p>

      {/* URL ingest */}
      <section className="mb-8 bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 rounded p-4">
        <h2 className="text-sm font-medium mb-2">{t.ingest.urlSectionTitle}</h2>
        <form onSubmit={submitUrl} className="space-y-2">
          <input
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder={t.ingest.urlPlaceholder}
            className="w-full px-3 py-2 text-sm bg-zinc-50 dark:bg-zinc-800 border border-zinc-300 dark:border-zinc-700 rounded focus:outline-none focus:ring-1 focus:ring-orange-500"
            disabled={pending}
          />
          <div className="flex items-center justify-between">
            <label className="text-xs flex items-center gap-1.5 text-zinc-600 dark:text-zinc-400">
              <input
                type="checkbox"
                checked={force}
                onChange={(e) => setForce(e.target.checked)}
              />
              {t.ingest.forceLabel}
            </label>
            <button
              type="submit"
              disabled={pending || !url.trim()}
              className="px-4 py-1.5 text-sm bg-orange-500 hover:bg-orange-600 text-white rounded disabled:opacity-50"
            >
              {pending ? t.ingest.processing : t.ingest.ingestBtn}
            </button>
          </div>
        </form>
      </section>

      {/* PDF 업로드 */}
      <section className="mb-8 bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 rounded p-4">
        <h2 className="text-sm font-medium mb-2">{t.ingest.pdfSectionTitle}</h2>
        <form onSubmit={submitPdf} className="space-y-2">
          <input
            id="pdf-input"
            type="file"
            accept="application/pdf,.pdf"
            onChange={(e) => setPdfFile(e.target.files?.[0] || null)}
            className="w-full text-xs text-zinc-600 dark:text-zinc-400 file:mr-3 file:py-1.5 file:px-3 file:rounded file:border-0 file:text-xs file:bg-zinc-200 dark:file:bg-zinc-700 file:text-zinc-700 dark:file:text-zinc-200 hover:file:bg-zinc-300"
            disabled={pending}
          />
          {pdfFile && (
            <div className="text-xs text-zinc-500">
              {pdfFile.name} · {(pdfFile.size / 1024).toFixed(0)} KB
            </div>
          )}
          <button
            type="submit"
            disabled={pending || !pdfFile}
            className="px-4 py-1.5 text-sm bg-orange-500 hover:bg-orange-600 text-white rounded disabled:opacity-50"
          >
            {pending ? t.ingest.uploading : t.ingest.uploadBtn}
          </button>
        </form>
        <p className="text-[10px] text-zinc-400 mt-2">{t.ingest.pdfNote}</p>
      </section>

      {/* 최근 결과 로그 */}
      <section className="bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 rounded p-4">
        <h2 className="text-sm font-medium mb-2">{t.ingest.recentSectionTitle}</h2>
        {log.length === 0 ? (
          <div className="text-xs text-zinc-500">{t.ingest.recentEmpty}</div>
        ) : (
          <ul className="space-y-1.5">
            {log.map((entry, i) => (
              <li
                key={i}
                className={`text-xs border-l-2 pl-2 ${
                  entry.kind === "ok"
                    ? "border-green-500"
                    : "border-red-500"
                }`}
              >
                <div className="flex items-center gap-2">
                  <span className="text-zinc-400 text-[10px]">{entry.ts}</span>
                  <span>{entry.msg}</span>
                </div>
                {entry.detail && (
                  <div className="text-[10px] text-zinc-500 font-mono mt-0.5">
                    {entry.detail}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}
