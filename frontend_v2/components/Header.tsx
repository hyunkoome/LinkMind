"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import ThemeToggle from "@/components/ThemeToggle";
import { useT } from "@/lib/i18n/context";

const NAV_HREFS = [
  { href: "/", icon: "🔮", key: "graph" as const },
  { href: "/ingest", icon: "📥", key: "ingest" as const },
  { href: "/search", icon: "🔍", key: "search" as const },
  { href: "/settings", icon: "⚙️", key: "settings" as const },
];

export default function Header() {
  const pathname = usePathname();
  const { locale, setLocale, t } = useT();

  return (
    <header className="shrink-0 h-12 border-b border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 flex items-center px-4 gap-1">
      <Link
        href="/"
        className="text-base font-semibold text-orange-600 dark:text-orange-400 mr-4"
      >
        {t.app.title}
      </Link>
      <nav className="flex gap-1">
        {NAV_HREFS.map((item) => {
          const active =
            item.href === "/"
              ? pathname === "/"
              : pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`text-xs px-3 py-1.5 rounded transition ${
                active
                  ? "bg-orange-100 dark:bg-orange-900/30 text-orange-700 dark:text-orange-300 font-medium"
                  : "text-zinc-600 dark:text-zinc-300 hover:bg-zinc-100 dark:hover:bg-zinc-800"
              }`}
            >
              <span className="mr-1">{item.icon}</span>
              {t.nav[item.key]}
            </Link>
          );
        })}
      </nav>

      <div className="ml-auto flex items-center gap-2">
        {/* Theme toggle — Next.js dev tools 의 Theme 메뉴 대체 */}
        <ThemeToggle />

        {/* Language toggle */}
        <div
          className="flex items-center text-[10px] bg-zinc-100 dark:bg-zinc-800 rounded p-0.5"
          aria-label={t.locale.toggleAria}
        >
          <button
            type="button"
            onClick={() => setLocale("ko")}
            className={`px-2 py-0.5 rounded transition ${
              locale === "ko"
                ? "bg-orange-500 text-white font-medium"
                : "text-zinc-600 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100"
            }`}
          >
            한
          </button>
          <button
            type="button"
            onClick={() => setLocale("en")}
            className={`px-2 py-0.5 rounded transition ${
              locale === "en"
                ? "bg-orange-500 text-white font-medium"
                : "text-zinc-600 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100"
            }`}
          >
            EN
          </button>
        </div>
        <div className="text-[10px] text-zinc-400 hidden md:block">
          Phase 2.5 · {t.app.tagline}
        </div>
      </div>
    </header>
  );
}
