"use client";

/**
 * wiki body markdown 렌더 — react-markdown 기반 (2026-06-04 재작성).
 *
 * 옛 자체 경량 파서는 표(GFM table)·LaTeX 수식을 못 그려 깨졌다 ("react-markdown 도입"
 * 이라고 코드에 예고돼 있던 시점). 이제 표준 파이프라인으로 전환:
 *   - remark-gfm   : 표 / 취소선 / 자동링크 / task list
 *   - remark-math + rehype-katex : $...$ inline, $$...$$ block LaTeX
 * + LinkMind 고유 확장 보존:
 *   - [[slug]]  → /wiki/{slug} wikilink (오렌지 점선)
 *   - [N]       → #source-N citation (위첨자 anchor)
 *   - /files/{hash} 상대경로 이미지 → backend(API_BASE)로 절대화 (frontend 404 방지)
 *
 * [[slug]]·[N] 은 markdown 표준이 아니라, react-markdown 에 넘기기 전에 표준 링크
 * 문법으로 전처리(preprocess)한 뒤 a 컴포넌트에서 분기 렌더한다.
 */

import Link from "next/link";
import React from "react";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

import "katex/dist/katex.min.css";

import { resolveAssetUrl } from "@/lib/api";

interface Props {
  body: string;
  className?: string;
}

/**
 * LinkMind 확장 문법 → 표준 markdown 링크로 전처리.
 *   [[slug]] → [[[slug]]](/wiki/slug)   (CommonMark 는 균형 잡힌 중첩 대괄호 허용 →
 *                                        링크 텍스트가 "[[slug]]" 로 보존됨)
 *   [12]     → [12](#source-12)         (이미 링크인 "](..." 형태는 negative lookahead 로 제외)
 * 순서 주의: wikilink 를 먼저 치환해야 citation 정규식이 슬러그 속 숫자를 안 건드림.
 */
function preprocess(md: string): string {
  let out = md.replace(/\[\[([^\]]+)\]\]/g, (_m, slug: string) => {
    const s = slug.trim();
    return `[[[${s}]]](/wiki/${encodeURIComponent(s)})`;
  });
  // [N] citation — 뒤에 '(' 가 오면(이미 markdown 링크) 건드리지 않음.
  out = out.replace(/\[(\d+)\](?!\()/g, (_m, n: string) => `[${n}](#source-${n})`);
  return out;
}

export default function WikiBody({ body, className = "" }: Props) {
  if (!body) {
    return (
      <div className={`text-zinc-500 dark:text-zinc-400 italic ${className}`}>
        (본문 없음 — 사용자가 페이지 첫 열 때 자동 합성)
      </div>
    );
  }

  const processed = preprocess(body);

  return (
    <div className={`wiki-body text-sm text-zinc-700 dark:text-zinc-300 leading-relaxed ${className}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex]}
        components={{
          h1: ({ children }) => (
            <h1 className="text-2xl font-bold mt-6 mb-3 text-zinc-900 dark:text-zinc-100">{children}</h1>
          ),
          h2: ({ children }) => (
            <h2 className="text-xl font-semibold mt-5 mb-2 text-zinc-800 dark:text-zinc-200 border-b border-zinc-200 dark:border-zinc-700 pb-1">
              {children}
            </h2>
          ),
          h3: ({ children }) => (
            <h3 className="text-base font-semibold mt-3 mb-1 text-zinc-800 dark:text-zinc-200">{children}</h3>
          ),
          p: ({ children }) => <p className="my-1.5 leading-relaxed">{children}</p>,
          ul: ({ children }) => <ul className="list-disc pl-6 my-2 space-y-1">{children}</ul>,
          ol: ({ children }) => <ol className="list-decimal pl-6 my-2 space-y-1">{children}</ol>,
          li: ({ children }) => <li>{children}</li>,
          blockquote: ({ children }) => (
            <blockquote className="border-l-4 border-orange-300 dark:border-orange-700 pl-3 my-2 italic text-zinc-600 dark:text-zinc-400">
              {children}
            </blockquote>
          ),
          strong: ({ children }) => (
            <strong className="font-semibold text-zinc-900 dark:text-zinc-100">{children}</strong>
          ),
          code: ({ className: cls, children }) => {
            // inline code 는 className 없음, fenced block 은 language-* className.
            const isBlock = !!cls;
            if (isBlock) {
              return <code className={cls}>{children}</code>;
            }
            return (
              <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-[0.85em] font-mono text-pink-600 dark:text-pink-400">
                {children}
              </code>
            );
          },
          pre: ({ children }) => (
            <pre className="bg-zinc-100 dark:bg-zinc-800 rounded p-3 my-2 text-xs overflow-x-auto">{children}</pre>
          ),
          // GFM 표 — 테두리 + 헤더 음영 + 가로 스크롤.
          table: ({ children }) => (
            <div className="my-3 overflow-x-auto">
              <table className="w-full text-xs border-collapse border border-zinc-300 dark:border-zinc-700">
                {children}
              </table>
            </div>
          ),
          thead: ({ children }) => <thead className="bg-zinc-100 dark:bg-zinc-800">{children}</thead>,
          th: ({ children }) => (
            <th className="border border-zinc-300 dark:border-zinc-700 px-2 py-1 text-left font-semibold text-zinc-800 dark:text-zinc-200">
              {children}
            </th>
          ),
          td: ({ children }) => (
            <td className="border border-zinc-300 dark:border-zinc-700 px-2 py-1 align-top">{children}</td>
          ),
          img: ({ src }) => (
            // eslint-disable-next-line @next/next/no-img-element — 동적 외부(backend) 이미지
            // alt 는 일부러 비운다("그림"). 본문 markdown 의 alt 에 전체 캡션이 들어간
            // 옛 위키도 있는데, 이미지가 깨지면 그 alt(=캡션)가 노출돼 바로 아래 *캡션*
            // 줄과 합쳐 캡션이 2번 보였다(2026-06-04). 캡션은 *...* 줄로만 보여준다.
            <img
              src={resolveAssetUrl(typeof src === "string" ? src : "")}
              alt="그림"
              className="my-3 max-w-full rounded border border-zinc-200 dark:border-zinc-700"
              loading="lazy"
            />
          ),
          a: ({ href, children }) => {
            const url = href || "";
            // [[slug]] wikilink — /wiki/{slug} 내부 라우팅 (오렌지 점선)
            if (url.startsWith("/wiki/")) {
              return (
                <Link
                  href={url}
                  className="text-orange-600 dark:text-orange-400 underline decoration-dotted hover:decoration-solid"
                >
                  {children}
                </Link>
              );
            }
            // [N] citation — #source-N anchor (위첨자)
            if (url.startsWith("#source-")) {
              const n = url.slice("#source-".length);
              return (
                <a
                  href={url}
                  className="text-blue-600 dark:text-blue-400 text-xs align-super px-0.5 hover:underline"
                >
                  [{n}]
                </a>
              );
            }
            // 일반 외부 링크
            return (
              <a
                href={url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-blue-600 dark:text-blue-400 hover:underline"
              >
                {children}
              </a>
            );
          },
        }}
      >
        {processed}
      </ReactMarkdown>
    </div>
  );
}
