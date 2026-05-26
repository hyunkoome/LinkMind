"use client";

import { useEffect, useState } from "react";

import { useT } from "@/lib/i18n/context";

type Theme = "light" | "dark" | "system";

const LS_KEY = "linkmind:theme";

function applyTheme(t: Theme) {
  const root = document.documentElement;
  root.classList.remove("light", "dark");
  if (t === "system") {
    const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    if (prefersDark) root.classList.add("dark");
  } else {
    root.classList.add(t);
  }
}

export default function ThemeToggle() {
  const { locale } = useT();
  const [theme, setTheme] = useState<Theme>("system");

  // 초기 — localStorage 또는 'system'. layout.tsx 의 inline script 가 flash 방지함.
  useEffect(() => {
    try {
      const v = window.localStorage.getItem(LS_KEY) as Theme | null;
      if (v === "light" || v === "dark") {
        setTheme(v);
      } else {
        setTheme("system");
      }
    } catch {
      /* ignore */
    }
  }, []);

  // theme 변경 시 적용 + localStorage 저장
  const cycle = () => {
    const order: Theme[] = ["system", "light", "dark"];
    const idx = order.indexOf(theme);
    const next = order[(idx + 1) % order.length];
    setTheme(next);
    try {
      if (next === "system") window.localStorage.removeItem(LS_KEY);
      else window.localStorage.setItem(LS_KEY, next);
    } catch {
      /* ignore */
    }
    applyTheme(next);
  };

  // OS 모드 변경 감지 (system 일 때만 반영)
  useEffect(() => {
    if (theme !== "system") return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const handler = () => applyTheme("system");
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, [theme]);

  const labels: Record<Theme, { icon: string; ko: string; en: string }> = {
    light: { icon: "☀️", ko: "라이트", en: "Light" },
    dark: { icon: "🌙", ko: "다크", en: "Dark" },
    system: { icon: "🖥", ko: "시스템", en: "System" },
  };
  const cur = labels[theme];

  return (
    <button
      type="button"
      onClick={cycle}
      className="text-xs px-2 py-1 rounded border border-zinc-300 dark:border-zinc-700 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition flex items-center gap-1"
      title={
        locale === "ko"
          ? "테마 전환 (라이트 / 다크 / 시스템)"
          : "Switch theme (light / dark / system)"
      }
    >
      <span>{cur.icon}</span>
      <span className="text-zinc-700 dark:text-zinc-200">{cur[locale]}</span>
    </button>
  );
}
