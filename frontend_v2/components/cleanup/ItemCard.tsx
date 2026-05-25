"use client";

import { API_BASE, type ItemListCard } from "@/lib/api";
import { useT } from "@/lib/i18n/context";

const SOURCE_COLOR: Record<string, string> = {
  pdf: "bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-300",
  url: "bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300",
  github: "bg-purple-100 dark:bg-purple-900/30 text-purple-700 dark:text-purple-300",
  youtube: "bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-300",
  youtube_playlist: "bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-300",
  arxiv: "bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-300",
  document: "bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300",
  telegram: "bg-cyan-100 dark:bg-cyan-900/30 text-cyan-700 dark:text-cyan-300",
  slack: "bg-violet-100 dark:bg-violet-900/30 text-violet-700 dark:text-violet-300",
};

const KIND_COLOR: Record<string, string> = {
  image_no_ocr: "bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300",
  extraction_failed: "bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-300",
  binary_no_extract: "bg-zinc-200 dark:bg-zinc-800 text-zinc-700 dark:text-zinc-300",
  short_raw: "bg-orange-100 dark:bg-orange-900/30 text-orange-700 dark:text-orange-300",
};

interface Props {
  item: ItemListCard;
  selected: boolean;
  onSelect: () => void;
}

export default function ItemCard({ item, selected, onSelect }: Props) {
  const { locale } = useT();
  const url = item.source_url
    ? item.source_url.startsWith("/")
      ? `${API_BASE}${item.source_url}`
      : item.source_url
    : null;

  // 이미지 첨부 — image_no_ocr 케이스의 thumbnail
  const imageAttach = item.attachments.find(
    (a) => a.mime_type?.startsWith("image/"),
  );
  const thumbUrl = imageAttach
    ? `${API_BASE}/files/${imageAttach.file_hash}`
    : null;

  return (
    <li
      className={`bg-white dark:bg-zinc-900 border rounded p-3 cursor-pointer transition ${
        selected
          ? "border-orange-500 ring-1 ring-orange-500"
          : "border-zinc-200 dark:border-zinc-800 hover:border-zinc-300 dark:hover:border-zinc-700"
      }`}
      onClick={onSelect}
    >
      <div className="flex items-start gap-3">
        {thumbUrl && (
          // 이미지 첨부 thumbnail — image_no_ocr 케이스 시각화
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={thumbUrl}
            alt={item.title || "image"}
            className="w-20 h-20 object-cover rounded shrink-0 bg-zinc-100 dark:bg-zinc-800"
            loading="lazy"
          />
        )}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5 mb-1 flex-wrap">
            <span
              className={`text-[10px] px-1.5 py-0.5 rounded ${
                SOURCE_COLOR[item.source_type] ||
                "bg-zinc-100 dark:bg-zinc-800 text-zinc-700 dark:text-zinc-300"
              }`}
            >
              {item.source_type}
            </span>
            {item.fetch_error_kind && (
              <span
                className={`text-[10px] px-1.5 py-0.5 rounded ${
                  KIND_COLOR[item.fetch_error_kind] ||
                  "bg-zinc-100 dark:bg-zinc-800 text-zinc-700"
                }`}
              >
                {item.fetch_error_kind}
              </span>
            )}
            {item.domain && (
              <span className="text-[10px] text-zinc-500 font-mono">
                {item.domain}
              </span>
            )}
            {item.has_user_notes && (
              <span
                className="text-[10px] text-cyan-700 dark:text-cyan-400"
                title={locale === "ko" ? "메모 있음" : "Has notes"}
              >
                📝
              </span>
            )}
            <span className="ml-auto text-[10px] text-zinc-400 font-mono">
              {item.raw_length.toLocaleString()} chars
            </span>
          </div>

          <h3 className="text-sm font-medium break-words mb-1 line-clamp-2">
            {item.title || "(no title)"}
          </h3>

          {item.summary_preview && (
            <p className="text-xs text-zinc-600 dark:text-zinc-400 line-clamp-2 mb-1">
              {item.summary_preview}
            </p>
          )}
          {!item.summary_preview && item.raw_preview && (
            <p className="text-xs text-zinc-500 italic line-clamp-2 mb-1">
              {item.raw_preview}
            </p>
          )}
          {item.fetch_error_message && (
            <p className="text-[11px] text-red-600 dark:text-red-400 mb-1">
              ⚠️ {item.fetch_error_message}
            </p>
          )}
          {item.user_notes_preview && (
            <p className="text-[11px] text-cyan-700 dark:text-cyan-400 line-clamp-2 mb-1 border-l-2 border-cyan-300 dark:border-cyan-700 pl-2">
              📝 {item.user_notes_preview}
            </p>
          )}

          <div className="flex items-center justify-between gap-2 flex-wrap">
            {url && (
              <a
                href={url}
                target="_blank"
                rel="noreferrer noopener"
                onClick={(e) => e.stopPropagation()}
                className="text-[11px] text-blue-600 dark:text-blue-400 hover:underline break-all line-clamp-1"
              >
                {url}
              </a>
            )}
            {item.tags.length > 0 && (
              <div className="flex gap-1 flex-wrap">
                {item.tags.slice(0, 6).map((tag) => (
                  <span
                    key={tag}
                    className="text-[9px] px-1 py-0.5 bg-zinc-100 dark:bg-zinc-800 rounded text-zinc-600 dark:text-zinc-400"
                  >
                    {tag}
                  </span>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </li>
  );
}
