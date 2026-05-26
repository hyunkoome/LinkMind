"use client";

/**
 * D10 wave-1g — wiki body markdown 단순 render.
 *
 * 의존성 최소 (react-markdown 안 씀) — h1~h3 / list / paragraph / inline code /
 * code block / [[slug]] wikilink auto-link / [N] citation 만 처리.
 * 완전한 markdown 이 필요해지면 wave-3 에 react-markdown 도입.
 *
 * [[slug]] 는 LinkMind 의 wikilink — /wiki/{slug} 로 자동 link.
 * [N] 은 citation — Sources 섹션의 source [N] 으로 anchor link.
 */

import Link from "next/link";
import React from "react";

interface Props {
  body: string;
  className?: string;
}

const WIKILINK_RE = /\[\[([^\]]+)\]\]/g;
const CITATION_RE = /\[(\d+)\]/g;

/**
 * 한 줄 안의 inline 처리 — [[slug]] / [N] / **bold** / `code` / link.
 * 가벼운 자체 파서 — 완전성 < 안전성.
 */
function renderInline(text: string, lineKey: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  let remaining = text;
  let idx = 0;

  while (remaining.length > 0) {
    // [[wikilink]]
    const wlMatch = WIKILINK_RE.exec(remaining);
    WIKILINK_RE.lastIndex = 0; // reset for repeated calls

    // [N] citation
    const citMatch = CITATION_RE.exec(remaining);
    CITATION_RE.lastIndex = 0;

    // 가장 먼저 나오는 패턴 선택
    let nextIdx = remaining.length;
    let nextType: "wl" | "cit" | null = null;
    if (wlMatch && wlMatch.index < nextIdx) {
      nextIdx = wlMatch.index;
      nextType = "wl";
    }
    if (citMatch && citMatch.index < nextIdx) {
      nextIdx = citMatch.index;
      nextType = "cit";
    }

    if (nextType === null) {
      // 남은 텍스트 그대로
      if (remaining) nodes.push(<span key={`${lineKey}-t${idx}`}>{remaining}</span>);
      break;
    }

    // 매칭 앞쪽 텍스트
    if (nextIdx > 0) {
      nodes.push(<span key={`${lineKey}-t${idx}`}>{remaining.slice(0, nextIdx)}</span>);
      idx += 1;
    }

    if (nextType === "wl" && wlMatch) {
      const slug = wlMatch[1].trim();
      nodes.push(
        <Link
          key={`${lineKey}-wl${idx}`}
          href={`/wiki/${encodeURIComponent(slug)}`}
          className="text-orange-600 dark:text-orange-400 underline decoration-dotted hover:decoration-solid"
        >
          [[{slug}]]
        </Link>,
      );
      remaining = remaining.slice(nextIdx + wlMatch[0].length);
    } else if (nextType === "cit" && citMatch) {
      const num = citMatch[1];
      nodes.push(
        <a
          key={`${lineKey}-cit${idx}`}
          href={`#source-${num}`}
          className="text-blue-600 dark:text-blue-400 text-xs align-super px-0.5 hover:underline"
        >
          [{num}]
        </a>,
      );
      remaining = remaining.slice(nextIdx + citMatch[0].length);
    } else {
      // unreachable
      break;
    }
    idx += 1;
  }
  return nodes;
}

export default function WikiBody({ body, className = "" }: Props) {
  if (!body) {
    return (
      <div className={`text-zinc-500 dark:text-zinc-400 italic ${className}`}>
        (본문 없음 — 사용자가 페이지 첫 열 때 자동 합성)
      </div>
    );
  }

  const lines = body.split("\n");
  const blocks: React.ReactNode[] = [];
  let listBuf: string[] = [];
  let codeBuf: string[] = [];
  let inCode = false;

  // 매 push 마다 unique key — flushList + 새 블록이 같은 line index 라도 충돌 X.
  let blockKey = 0;
  const nextKey = () => `b${blockKey++}`;

  const flushList = () => {
    if (listBuf.length === 0) return;
    const k = nextKey();
    blocks.push(
      <ul key={k} className="list-disc pl-6 my-2 space-y-1 text-sm text-zinc-700 dark:text-zinc-300">
        {listBuf.map((item, i) => (
          <li key={`${k}-${i}`}>{renderInline(item, `${k}-${i}`)}</li>
        ))}
      </ul>,
    );
    listBuf = [];
  };

  const flushCode = () => {
    if (codeBuf.length === 0) return;
    const k = nextKey();
    blocks.push(
      <pre key={k} className="bg-zinc-100 dark:bg-zinc-800 rounded p-3 my-2 text-xs overflow-x-auto">
        <code>{codeBuf.join("\n")}</code>
      </pre>,
    );
    codeBuf = [];
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    // code fence
    if (line.trim().startsWith("```")) {
      if (inCode) {
        flushCode();
        inCode = false;
      } else {
        flushList();
        inCode = true;
      }
      continue;
    }
    if (inCode) {
      codeBuf.push(line);
      continue;
    }

    // 빈 줄 — 블록 구분
    if (line.trim() === "") {
      flushList();
      continue;
    }

    // 헤더
    if (line.startsWith("# ")) {
      flushList();
      const k = nextKey();
      blocks.push(
        <h1 key={k} className="text-2xl font-bold mt-6 mb-3 text-zinc-900 dark:text-zinc-100">
          {renderInline(line.slice(2), k)}
        </h1>,
      );
      continue;
    }
    if (line.startsWith("## ")) {
      flushList();
      const k = nextKey();
      blocks.push(
        <h2 key={k} className="text-xl font-semibold mt-5 mb-2 text-zinc-800 dark:text-zinc-200 border-b border-zinc-200 dark:border-zinc-700 pb-1">
          {renderInline(line.slice(3), k)}
        </h2>,
      );
      continue;
    }
    if (line.startsWith("### ")) {
      flushList();
      const k = nextKey();
      blocks.push(
        <h3 key={k} className="text-base font-semibold mt-3 mb-1 text-zinc-800 dark:text-zinc-200">
          {renderInline(line.slice(4), k)}
        </h3>,
      );
      continue;
    }

    // 인용
    if (line.startsWith("> ")) {
      flushList();
      const k = nextKey();
      blocks.push(
        <blockquote key={k} className="border-l-4 border-orange-300 dark:border-orange-700 pl-3 my-2 italic text-zinc-600 dark:text-zinc-400">
          {renderInline(line.slice(2), k)}
        </blockquote>,
      );
      continue;
    }

    // 리스트 항목
    if (line.startsWith("- ") || line.startsWith("* ")) {
      listBuf.push(line.slice(2));
      continue;
    }

    // 일반 paragraph
    flushList();
    const k = nextKey();
    blocks.push(
      <p key={k} className="my-1.5 text-sm text-zinc-700 dark:text-zinc-300 leading-relaxed">
        {renderInline(line, k)}
      </p>,
    );
  }
  flushList();
  flushCode();

  return <div className={`wiki-body ${className}`}>{blocks}</div>;
}
