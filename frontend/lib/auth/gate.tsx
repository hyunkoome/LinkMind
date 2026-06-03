"use client";

// 인증 게이트 (2026-06-03) — 세션 확인 후 Header+children / 로그인 / 강제 자격변경.
//   - loading                 : 중앙 스피너
//   - 미인증 + 보호 경로       : null (AuthProvider 가 /login 으로 replace 중)
//   - 미인증 + /login          : Header 없이 children (로그인/bootstrap 폼)
//   - 인증 + must_change       : ChangeCredentials 강제 (Header 없이)
//   - 인증                     : Header + children

import { usePathname } from "next/navigation";

import Header from "@/components/Header";
import { ChangeCredentials } from "@/lib/auth/change-credentials";
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

  // 미인증.
  if (!user) {
    if (isPublic) return <div className="flex-1 min-h-0 flex flex-col">{children}</div>;
    return null; // 보호 경로 → redirect 진행 중
  }

  // 인증됨 — 첫 로그인 강제 자격 변경이 최우선 (어느 경로든).
  if (user.must_change_password) {
    return (
      <div className="flex-1 min-h-0 flex flex-col">
        <ChangeCredentials />
      </div>
    );
  }

  // /login 인데 이미 로그인됨 → login page 가 /ask 로 redirect 중.
  if (isPublic) return <div className="flex-1 min-h-0 flex flex-col">{children}</div>;

  // 정상 — Header + children.
  return (
    <>
      <Header />
      <div className="flex-1 min-h-0 flex flex-col">{children}</div>
    </>
  );
}
