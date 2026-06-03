"use client";

// 로그인 / 첫 관리자 등록(bootstrap) 페이지 (2026-06-03 단계 A).
// 운영 모델: self-signup 없음. 멤버 계정은 루트 관리자가 발급(Settings).
//   - 인스턴스에 user 0명 → "조직 만들기"(첫 관리자 등록) 폼 (1회)
//   - 그 외           → 로그인 폼
// 이미 로그인된 상태면 /ask 로.

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { bootstrapNeeded } from "@/lib/api";
import { useAuth } from "@/lib/auth/context";
import { useT } from "@/lib/i18n/context";

export default function LoginPage() {
  const { login, bootstrap, user, loading } = useAuth();
  const { locale } = useT();
  const router = useRouter();
  const ko = locale === "ko";

  const [checking, setChecking] = useState(true);
  const [needsBootstrap, setNeedsBootstrap] = useState(false);

  const [orgName, setOrgName] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // 이미 인증됐으면 메인으로.
  useEffect(() => {
    if (!loading && user) router.replace("/ask");
  }, [loading, user, router]);

  // 첫 관리자 등록 필요 여부 확인.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const needed = await bootstrapNeeded();
        if (!cancelled) setNeedsBootstrap(needed);
      } catch {
        if (!cancelled) setNeedsBootstrap(false); // 확인 실패 시 로그인 폼.
      } finally {
        if (!cancelled) setChecking(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      if (needsBootstrap) {
        await bootstrap(orgName.trim(), email.trim(), password, displayName);
      } else {
        await login(email.trim(), password);
      }
      router.replace("/ask");
    } catch {
      setError(
        needsBootstrap
          ? ko
            ? "조직 생성에 실패했습니다 (입력값을 확인하세요)"
            : "Failed to create organization (check your input)"
          : ko
            ? "이메일 또는 비밀번호가 올바르지 않습니다"
            : "Invalid email or password",
      );
    } finally {
      setSubmitting(false);
    }
  };

  const inputCls =
    "text-sm px-3 py-2 rounded-lg border border-zinc-300 dark:border-zinc-700 bg-transparent text-zinc-900 dark:text-zinc-100 focus:outline-none focus:ring-2 focus:ring-orange-400";

  return (
    <div className="flex-1 flex items-center justify-center px-4">
      <form
        onSubmit={onSubmit}
        className="w-full max-w-sm bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 rounded-xl p-7 shadow-sm flex flex-col gap-4"
      >
        <div className="text-center mb-1">
          <div className="text-xl font-semibold text-orange-600 dark:text-orange-400">
            LinkMind
          </div>
          <div className="text-xs text-zinc-400 mt-1">
            {checking
              ? "…"
              : needsBootstrap
                ? ko
                  ? "조직 만들기 (첫 관리자 등록)"
                  : "Create organization (first admin)"
                : ko
                  ? "로그인"
                  : "Sign in"}
          </div>
        </div>

        {/* bootstrap 전용 — 조직명 */}
        {needsBootstrap && (
          <label className="flex flex-col gap-1 text-xs text-zinc-500 dark:text-zinc-400">
            {ko ? "조직 이름" : "Organization name"}
            <input
              type="text"
              required
              value={orgName}
              onChange={(e) => setOrgName(e.target.value)}
              className={inputCls}
            />
          </label>
        )}

        <label className="flex flex-col gap-1 text-xs text-zinc-500 dark:text-zinc-400">
          {ko ? "이메일" : "Email"}
          <input
            type="email"
            autoComplete="username"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className={inputCls}
          />
        </label>

        {needsBootstrap && (
          <label className="flex flex-col gap-1 text-xs text-zinc-500 dark:text-zinc-400">
            {ko ? "이름 (선택)" : "Name (optional)"}
            <input
              type="text"
              autoComplete="name"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              className={inputCls}
            />
          </label>
        )}

        <label className="flex flex-col gap-1 text-xs text-zinc-500 dark:text-zinc-400">
          {ko ? "비밀번호" : "Password"}
          <input
            type="password"
            autoComplete={needsBootstrap ? "new-password" : "current-password"}
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className={inputCls}
          />
          {needsBootstrap && (
            <span className="text-[10px] text-zinc-400">
              {ko ? "6자 이상" : "6+ characters"}
            </span>
          )}
        </label>

        {error && (
          <div className="text-xs text-red-500 bg-red-50 dark:bg-red-900/20 rounded px-3 py-2">
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={submitting || checking}
          className="mt-1 text-sm font-medium px-4 py-2 rounded-lg bg-orange-500 hover:bg-orange-600 text-white transition disabled:opacity-50"
        >
          {submitting
            ? ko
              ? "처리 중…"
              : "Please wait…"
            : needsBootstrap
              ? ko
                ? "조직 만들기"
                : "Create organization"
              : ko
                ? "로그인"
                : "Sign in"}
        </button>

        {!needsBootstrap && !checking && (
          <div className="text-[10px] text-zinc-400 text-center mt-1">
            {ko
              ? "계정이 필요하면 조직 관리자에게 문의하세요."
              : "Need an account? Contact your organization admin."}
          </div>
        )}
      </form>
    </div>
  );
}
