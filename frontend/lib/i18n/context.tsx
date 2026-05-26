"use client";

// 한국어/영어 토글 context. localStorage 에 영구 저장 — 다음 방문 시 유지.

import { createContext, useContext, useEffect, useState } from "react";

import { type Dict, type Locale, dict } from "./dict";

interface LocaleContextValue {
  locale: Locale;
  setLocale: (l: Locale) => void;
  t: Dict;
}

const LocaleContext = createContext<LocaleContextValue | null>(null);

const LS_KEY = "linkmind:locale";

function detectInitialLocale(): Locale {
  if (typeof window === "undefined") return "ko";
  try {
    const saved = window.localStorage.getItem(LS_KEY);
    if (saved === "ko" || saved === "en") return saved;
  } catch {
    /* ignore */
  }
  // 브라우저 언어 감지 — ko* 면 ko, 그 외엔 en
  const nav = window.navigator?.language?.toLowerCase() || "";
  return nav.startsWith("ko") ? "ko" : "en";
}

export function LocaleProvider({ children }: { children: React.ReactNode }) {
  // SSR safety — 초기 server render 는 ko (default). client 가 hydrate 후 localStorage 적용.
  const [locale, setLocaleState] = useState<Locale>("ko");

  useEffect(() => {
    setLocaleState(detectInitialLocale());
  }, []);

  const setLocale = (l: Locale) => {
    setLocaleState(l);
    try {
      window.localStorage.setItem(LS_KEY, l);
    } catch {
      /* ignore */
    }
  };

  return (
    <LocaleContext.Provider value={{ locale, setLocale, t: dict[locale] }}>
      {children}
    </LocaleContext.Provider>
  );
}

export function useT(): LocaleContextValue {
  const ctx = useContext(LocaleContext);
  if (!ctx) {
    // LocaleProvider 밖에서 호출되면 default (ko) 로 graceful fallback.
    // 보통 layout.tsx 가 wrap 하지만 서버 컴포넌트 등 예외 케이스 보호.
    return {
      locale: "ko",
      setLocale: () => {},
      t: dict.ko,
    };
  }
  return ctx;
}
