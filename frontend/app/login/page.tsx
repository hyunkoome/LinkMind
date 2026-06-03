"use client";

// 로그인 / 회원가입 페이지 (2026-06-03 단계 A).
//   - 로그인: POST /auth/login → httpOnly 쿠키
//   - 회원가입: POST /auth/register → 새 user + 본인 personal space 자동 생성 + 자동 로그인
// 이미 로그인된 상태로 들어오면 /ask 로. 성공 시에도 /ask.

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { useAuth } from "@/lib/auth/context";
import { useT } from "@/lib/i18n/context";

type Mode = "login" | "register";

export default function LoginPage() {
  const { login, register, user, loading } = useAuth();
  const { locale } = useT();
  const router = useRouter();
  const ko = locale === "ko";

  const [mode, setMode] = useState<Mode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const isRegister = mode === "register";

  // 이미 인증됐으면 메인으로.
  useEffect(() => {
    if (!loading && user) router.replace("/ask");
  }, [loading, user, router]);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      if (isRegister) {
        await register(email.trim(), password, displayName);
      } else {
        await login(email.trim(), password);
      }
      router.replace("/ask");
    } catch (err) {
      // backend detail(이미 등록된 이메일 등)을 최대한 노출.
      const msg = err instanceof Error ? err.message : "";
      if (isRegister && msg.includes("409")) {
        setError(ko ? "이미 등록된 이메일입니다" : "Email already registered");
      } else if (isRegister) {
        setError(
          ko
            ? "회원가입에 실패했습니다 (이메일 3자+ / 비밀번호 6자+ 확인)"
            : "Sign up failed (email 3+ chars / password 6+ chars)",
        );
      } else {
        setError(
          ko
            ? "이메일 또는 비밀번호가 올바르지 않습니다"
            : "Invalid email or password",
        );
      }
    } finally {
      setSubmitting(false);
    }
  };

  const switchMode = (m: Mode) => {
    setMode(m);
    setError(null);
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
        </div>

        {/* 로그인 / 회원가입 탭 */}
        <div className="flex bg-zinc-100 dark:bg-zinc-800 rounded-lg p-0.5 text-xs">
          <button
            type="button"
            onClick={() => switchMode("login")}
            className={`flex-1 py-1.5 rounded-md transition ${
              !isRegister
                ? "bg-white dark:bg-zinc-700 text-orange-600 dark:text-orange-300 font-medium shadow-sm"
                : "text-zinc-500 dark:text-zinc-400"
            }`}
          >
            {ko ? "로그인" : "Sign in"}
          </button>
          <button
            type="button"
            onClick={() => switchMode("register")}
            className={`flex-1 py-1.5 rounded-md transition ${
              isRegister
                ? "bg-white dark:bg-zinc-700 text-orange-600 dark:text-orange-300 font-medium shadow-sm"
                : "text-zinc-500 dark:text-zinc-400"
            }`}
          >
            {ko ? "회원가입" : "Sign up"}
          </button>
        </div>

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

        {isRegister && (
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
            autoComplete={isRegister ? "new-password" : "current-password"}
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className={inputCls}
          />
          {isRegister && (
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
          disabled={submitting}
          className="mt-1 text-sm font-medium px-4 py-2 rounded-lg bg-orange-500 hover:bg-orange-600 text-white transition disabled:opacity-50"
        >
          {submitting
            ? ko
              ? "처리 중…"
              : "Please wait…"
            : isRegister
              ? ko
                ? "회원가입"
                : "Sign up"
              : ko
                ? "로그인"
                : "Sign in"}
        </button>
      </form>
    </div>
  );
}
