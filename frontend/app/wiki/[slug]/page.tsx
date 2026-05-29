"use client";

/**
 * /wiki/[slug] — wiki page 상세 (전체 페이지).
 * 내용은 WikiDetailView (variant="page") 가 담당 — /wiki 우측 패널과 동일 컴포넌트 재사용.
 */

import { use } from "react";

import WikiDetailView from "@/components/wiki/WikiDetailView";

interface PageProps {
  // Next.js 16: params 는 Promise — `use()` 로 unwrap.
  params: Promise<{ slug: string }>;
}

export default function WikiDetailPage({ params }: PageProps) {
  const { slug } = use(params);
  return <WikiDetailView slug={slug} variant="page" />;
}
