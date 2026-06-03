"use client";

// 첫 로그인 강제 자격 변경 (2026-06-03) — must_change_password=true 면 AuthGate 가 표시.
// 루트가 발급한 멤버가 초기 비번을 자기 비번으로 바꾼다 (이메일은 선택).

import { useState } from "react";

import { useAuth } from "@/lib/auth/context";
import { useT } from "@/lib/i18n/context";

export function ChangeCredentials() {
  const { user, changeCredentials, logout } = useAuth();
  const { locale } = useT();
  const ko = locale === "ko";

  const [currentPw, setCurrentPw] = useState("");
  const [newEmail, setNewEmail] = useState(user?.email ?? "");
  const [newPw, setNewPw] = useState("");
  const [confirmPw, setConfirmPw] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (newPw !== confirmPw) {
      setError(ko ? "새 비밀번호가 일치하지 않습니다" : "Passwords do not match");
      return;
    }
    setSubmitting(true);
    try {
      const emailChanged = newEmail.trim() && newEmail.trim() !== user?.email;
      await changeCredentials(
        currentPw,
        newPw,
        emailChanged ? newEmail.trim() : undefined,
      );
    } catch (err) {
      const msg = err instanceof Error ? err.message : "";
      if (msg.includes("409")) {
        setError(ko ? "이미 사용 중인 이메일입니다" : "Email already in use");
      } else if (msg.includes("401")) {
        setError(ko ? "현재 비밀번호가 올바르지 않습니다" : "Current password is incorrect");
      } else {
        setError(ko ? "변경에 실패했습니다" : "Failed to update");
      }
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
            {ko ? "첫 로그인 — 자격 변경 필요" : "First login — update your credentials"}
          </div>
        </div>

        <label className="flex flex-col gap-1 text-xs text-zinc-500 dark:text-zinc-400">
          {ko ? "현재 비밀번호" : "Current password"}
          <input
            type="password"
            autoComplete="current-password"
            required
            value={currentPw}
            onChange={(e) => setCurrentPw(e.target.value)}
            className={inputCls}
          />
        </label>

        <label className="flex flex-col gap-1 text-xs text-zinc-500 dark:text-zinc-400">
          {ko ? "이메일" : "Email"}
          <input
            type="email"
            autoComplete="email"
            required
            value={newEmail}
            onChange={(e) => setNewEmail(e.target.value)}
            className={inputCls}
          />
        </label>

        <label className="flex flex-col gap-1 text-xs text-zinc-500 dark:text-zinc-400">
          {ko ? "새 비밀번호" : "New password"}
          <input
            type="password"
            autoComplete="new-password"
            required
            value={newPw}
            onChange={(e) => setNewPw(e.target.value)}
            className={inputCls}
          />
          <span className="text-[10px] text-zinc-400">
            {ko ? "6자 이상" : "6+ characters"}
          </span>
        </label>

        <label className="flex flex-col gap-1 text-xs text-zinc-500 dark:text-zinc-400">
          {ko ? "새 비밀번호 확인" : "Confirm new password"}
          <input
            type="password"
            autoComplete="new-password"
            required
            value={confirmPw}
            onChange={(e) => setConfirmPw(e.target.value)}
            className={inputCls}
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
              ? "변경 중…"
              : "Updating…"
            : ko
              ? "변경하고 계속"
              : "Update & continue"}
        </button>

        <button
          type="button"
          onClick={() => logout()}
          className="text-[11px] text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300"
        >
          {ko ? "로그아웃" : "Log out"}
        </button>
      </form>
    </div>
  );
}
