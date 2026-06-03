"use client";

// 로그인 페이지 (2026-06-03 단계 A) — id/pw → POST /auth/login → httpOnly 쿠키.
// 이미 로그인된 상태로 들어오면 /ask 로. 성공 시에도 /ask.

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { useAuth } from "@/lib/auth/context";
import { useT } from "@/lib/i18n/context";

export default function LoginPage() {
  const { login, user, loading } = useAuth();
  const { locale } = useT();
  const router = useRouter();
  const ko = locale === "ko";

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // 이미 인증됐으면 메인으로.
  useEffect(() => {
    if (!loading && user) router.replace("/ask");
  }, [loading, user, router]);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(email.trim(), password);
      router.replace("/ask");
    } catch {
      setError(
        ko
          ? "이메일 또는 비밀번호가 올바르지 않습니다"
          : "Invalid email or password",
      );
    } finally {
      setSubmitting(false);
    }
  };

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
            {ko ? "로그인" : "Sign in"}
          </div>
        </div>

        <label className="flex flex-col gap-1 text-xs text-zinc-500 dark:text-zinc-400">
          {ko ? "이메일" : "Email"}
          <input
            type="email"
            autoComplete="username"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="text-sm px-3 py-2 rounded-lg border border-zinc-300 dark:border-zinc-700 bg-transparent text-zinc-900 dark:text-zinc-100 focus:outline-none focus:ring-2 focus:ring-orange-400"
          />
        </label>

        <label className="flex flex-col gap-1 text-xs text-zinc-500 dark:text-zinc-400">
          {ko ? "비밀번호" : "Password"}
          <input
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="text-sm px-3 py-2 rounded-lg border border-zinc-300 dark:border-zinc-700 bg-transparent text-zinc-900 dark:text-zinc-100 focus:outline-none focus:ring-2 focus:ring-orange-400"
          />
        </label>

        {error && (
          <div className="text-xs text-red-500 bg-red-50 dark:bg-red-900/20 rounded px-3 py-2">
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={submitting}
          className="mt-1 text-sm font-medium px-4 py-2 rounded-lg bg-orange-500 hover:bg-orange-600 text-white transition disabled:opacity-50"
        >
          {submitting
            ? ko
              ? "로그인 중…"
              : "Signing in…"
            : ko
              ? "로그인"
              : "Sign in"}
        </button>
      </form>
    </div>
  );
}
