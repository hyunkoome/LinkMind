"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV = [
  { href: "/", label: "Graph", icon: "🔮" },
  { href: "/ingest", label: "Ingest", icon: "📥" },
  { href: "/search", label: "Search", icon: "🔍" },
  { href: "/settings", label: "Settings", icon: "⚙️" },
];

export default function Header() {
  const pathname = usePathname();
  return (
    <header className="shrink-0 h-12 border-b border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 flex items-center px-4 gap-1">
      <Link href="/" className="text-base font-semibold text-orange-600 dark:text-orange-400 mr-4">
        LinkMind
      </Link>
      <nav className="flex gap-1">
        {NAV.map((item) => {
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
              {item.label}
            </Link>
          );
        })}
      </nav>
      <div className="ml-auto text-[10px] text-zinc-400">
        Phase 2.5 · self-contained personal AI
      </div>
    </header>
  );
}
