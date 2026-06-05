"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import ThemeToggle from "@/components/ThemeToggle";
import { useAuth } from "@/lib/auth/context";
import { useT } from "@/lib/i18n/context";

// nav (2026-05-29): ask 가 LinkMind 메인 경로 (홈 '/'→ask redirect). 그래프 페이지는
// 규모/효용 문제로 제거 — 키워드↔위키 탐색은 /wiki 가 담당. 그래프 코드는 /graph 로 보류.
const NAV_HREFS = [
  { href: "/ask", icon: "🤖", key: "ask" as const },
  { href: "/wiki", icon: "📖", key: "wiki" as const },
  { href: "/admin/arxiv", icon: "🔭", key: "arxivAdmin" as const },
  { href: "/ingest", icon: "📥", key: "ingest" as const },
  { href: "/settings", icon: "⚙️", key: "settings" as const },
];

// nav 에서 admin/root 에게만 보이는 항목들 (조직 전역 영향 → 루트 전용).
const ADMIN_ONLY_NAV = new Set(["settings", "arxivAdmin"]);

export default function Header() {
  const pathname = usePathname();
  const { locale, setLocale, t } = useT();
  const { user, activeSpace, switchSpace, logout } = useAuth();
  // 설정은 루트 전용 — member 에겐 nav 에서 숨김.
  const isAdmin = activeSpace?.role === "owner" || activeSpace?.role === "admin";

  return (
    <header className="shrink-0 h-12 border-b border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 flex items-center px-4 gap-1">
      <Link
        href="/"
        className="text-base font-semibold text-orange-600 dark:text-orange-400 mr-4"
      >
        {t.app.title}
      </Link>
      <nav className="flex gap-1">
        {NAV_HREFS.filter((item) => !ADMIN_ONLY_NAV.has(item.key) || isAdmin).map((item) => {
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
        {/* 활성 space — 멤버 여러 space 면 전환 select, 1개면 라벨 (멀티테넌트 단계 A) */}
        {user &&
          (user.spaces.length > 1 ? (
            <select
              value={user.active_space_id}
              onChange={(e) => switchSpace(e.target.value)}
              className="text-[11px] bg-zinc-100 dark:bg-zinc-800 rounded px-2 py-1 text-zinc-700 dark:text-zinc-300"
              title={user.email}
            >
              {user.spaces.map((s) => (
                <option key={s.id} value={s.id}>
                  🗂 {s.name}
                </option>
              ))}
            </select>
          ) : (
            <span
              className="text-[11px] text-zinc-500 dark:text-zinc-400 hidden md:inline"
              title={user.email}
            >
              🗂 {activeSpace?.name}
            </span>
          ))}
        {/* 로그인 계정 (조직 이름 옆) */}
        {user && (
          <span
            className="text-[11px] text-zinc-400 hidden md:inline"
            title={locale === "ko" ? "로그인 계정" : "Signed in as"}
          >
            · {user.email}
          </span>
        )}
        {user && (
          <button
            type="button"
            onClick={() => logout()}
            className="text-[11px] px-2 py-1 rounded text-zinc-600 dark:text-zinc-300 hover:bg-zinc-100 dark:hover:bg-zinc-800"
            title={user.email}
          >
            {locale === "ko" ? "로그아웃" : "Logout"}
          </button>
        )}

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
