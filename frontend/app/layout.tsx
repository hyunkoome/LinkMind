import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";

import Header from "@/components/Header";
import { LocaleProvider } from "@/lib/i18n/context";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "LinkMind — Personal AI Engine",
  description: "Personal knowledge OS — 3D graph + 자동 ingest + sVLL 학습 데이터 누적",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="ko"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
      suppressHydrationWarning
    >
      <head>
        {/* 초기 dark class 결정 — body 렌더 전에 inline script 로 flash of wrong theme 방지 */}
        <script
          dangerouslySetInnerHTML={{
            __html: `
              try {
                var t = localStorage.getItem('linkmind:theme');
                var d = (t === 'dark') || (t !== 'light' && window.matchMedia('(prefers-color-scheme: dark)').matches);
                if (d) document.documentElement.classList.add('dark');
                else document.documentElement.classList.remove('dark');
                if (t === 'light' || t === 'dark') document.documentElement.classList.add(t);
              } catch(e) {}
            `,
          }}
        />
      </head>
      <body className="min-h-screen h-screen bg-zinc-50 text-zinc-900 dark:bg-zinc-950 dark:text-zinc-100 flex flex-col">
        <LocaleProvider>
          <Header />
          <div className="flex-1 min-h-0">{children}</div>
        </LocaleProvider>
      </body>
    </html>
  );
}
