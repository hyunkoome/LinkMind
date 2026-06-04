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

import { resolveAssetUrl } from "@/lib/api";

interface Props {
  body: string;
  className?: string;
}

// inline 패턴들 — 한 줄 안에서 가장 먼저 나오는 것부터 처리.
// 순서 주의: mdlink([text](url)) 를 citation([N]) 보다 먼저 검사해야 [1](url) 류가
// citation 으로 오인되지 않음 (동일 index 일 때 우선순위).
const WIKILINK_RE = /\[\[([^\]]+)\]\]/;          // [[slug]]
const MDLINK_RE = /\[([^\]]+)\]\(([^)\s]+)\)/;    // [text](url)
const BOLD_RE = /\*\*([^*]+)\*\*/;                // **bold**
const CODE_RE = /`([^`]+)`/;                      // `code`
const CITATION_RE = /\[(\d+)\]/;                  // [N]

type InlineKind = "wl" | "mdlink" | "bold" | "code" | "cit";

/**
 * 한 줄 안의 inline 처리 — [[slug]] / [text](url) / **bold** / `code` / [N].
 * 가벼운 자체 파서 — 완전성 < 안전성. 매 iteration 마다 남은 문자열에서 가장
 * 앞서 나오는 패턴을 골라 처리하고 그 뒤로 이어간다 (react-markdown 미도입).
 */
function renderInline(text: string, lineKey: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  let remaining = text;
  let idx = 0;

  // 우선순위 순서 (동일 index 충돌 시 앞쪽이 이김)
  const patterns: Array<{ kind: InlineKind; re: RegExp }> = [
    { kind: "wl", re: WIKILINK_RE },
    { kind: "mdlink", re: MDLINK_RE },
    { kind: "bold", re: BOLD_RE },
    { kind: "code", re: CODE_RE },
    { kind: "cit", re: CITATION_RE },
  ];

  while (remaining.length > 0) {
    let nextIdx = remaining.length;
    let nextKind: InlineKind | null = null;
    let nextMatch: RegExpMatchArray | null = null;

    for (const { kind, re } of patterns) {
      const m = remaining.match(re);
      if (m && m.index !== undefined && m.index < nextIdx) {
        nextIdx = m.index;
        nextKind = kind;
        nextMatch = m;
      }
    }

    if (nextKind === null || nextMatch === null) {
      if (remaining) nodes.push(<span key={`${lineKey}-t${idx}`}>{remaining}</span>);
      break;
    }

    // 매칭 앞쪽 텍스트
    if (nextIdx > 0) {
      nodes.push(<span key={`${lineKey}-t${idx}`}>{remaining.slice(0, nextIdx)}</span>);
      idx += 1;
    }

    const matchLen = nextMatch[0].length;
    if (nextKind === "wl") {
      const slug = nextMatch[1].trim();
      nodes.push(
        <Link
          key={`${lineKey}-wl${idx}`}
          href={`/wiki/${encodeURIComponent(slug)}`}
          className="text-orange-600 dark:text-orange-400 underline decoration-dotted hover:decoration-solid"
        >
          [[{slug}]]
        </Link>,
      );
    } else if (nextKind === "mdlink") {
      const label = nextMatch[1];
      const url = nextMatch[2];
      nodes.push(
        <a
          key={`${lineKey}-md${idx}`}
          href={url}
          target="_blank"
          rel="noopener noreferrer"
          className="text-blue-600 dark:text-blue-400 hover:underline"
        >
          {label}
        </a>,
      );
    } else if (nextKind === "bold") {
      nodes.push(
        <strong key={`${lineKey}-b${idx}`} className="font-semibold text-zinc-900 dark:text-zinc-100">
          {nextMatch[1]}
        </strong>,
      );
    } else if (nextKind === "code") {
      nodes.push(
        <code
          key={`${lineKey}-c${idx}`}
          className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-[0.85em] font-mono text-pink-600 dark:text-pink-400"
        >
          {nextMatch[1]}
        </code>,
      );
    } else {
      // citation [N]
      const num = nextMatch[1];
      nodes.push(
        <a
          key={`${lineKey}-cit${idx}`}
          href={`#source-${num}`}
          className="text-blue-600 dark:text-blue-400 text-xs align-super px-0.5 hover:underline"
        >
          [{num}]
        </a>,
      );
    }
    remaining = remaining.slice(nextIdx + matchLen);
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

    // 이미지 (figure) — ![alt](url) 단독 줄 → <img>. body 의 figure 가 '/files/{hash}'
    // 상대경로면 backend(API_BASE)로 절대화 (frontend 로 가면 404).
    const imgMatch = line.match(/^!\[([^\]]*)\]\(([^)\s]+)\)\s*$/);
    if (imgMatch) {
      flushList();
      const k = nextKey();
      blocks.push(
        // eslint-disable-next-line @next/next/no-img-element — 동적 외부(backend) 이미지
        <img
          key={k}
          src={resolveAssetUrl(imgMatch[2])}
          alt={imgMatch[1] || "figure"}
          className="my-3 max-w-full rounded border border-zinc-200 dark:border-zinc-700"
          loading="lazy"
        />,
      );
      continue;
    }

    // figure caption — *text* 단독 줄 (단일 별표 italic) → 작은 회색 캡션.
    const capMatch = line.match(/^\*([^*]+)\*\s*$/);
    if (capMatch) {
      flushList();
      const k = nextKey();
      blocks.push(
        <p key={k} className="text-xs text-zinc-500 dark:text-zinc-400 italic -mt-2 mb-4 text-center">
          {capMatch[1]}
        </p>,
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
