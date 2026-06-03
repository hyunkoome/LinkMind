"use client";

// 인증 게이트 (2026-06-03 단계 A) — 세션 확인 후 Header+children 또는 로그인 화면.
//   - loading       : 중앙 스피너 (깜빡임 방지)
//   - 미인증 + 보호  : null (AuthProvider 가 /login 으로 replace 중)
//   - /login         : Header 없이 children 만
//   - 인증됨         : Header + children (기존 layout 구조 유지)

import { usePathname } from "next/navigation";

import Header from "@/components/Header";
import { useAuth } from "@/lib/auth/context";

const PUBLIC_PATHS = ["/login"];

export function AuthGate({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const pathname = usePathname();
  const isPublic = PUBLIC_PATHS.includes(pathname);

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center text-zinc-400 text-sm">
        <span className="animate-pulse">LinkMind …</span>
      </div>
    );
  }

  // 보호 경로인데 미인증 → redirect 진행 중. 깜빡임 없이 빈 화면.
  if (!user && !isPublic) return null;

  // 로그인 페이지 — Header 없이.
  if (isPublic) {
    return <div className="flex-1 min-h-0 flex flex-col">{children}</div>;
  }

  // 인증됨 — 기존 layout 구조 (Header + flex-1 컨테이너).
  return (
    <>
      <Header />
      <div className="flex-1 min-h-0 flex flex-col">{children}</div>
    </>
  );
}
