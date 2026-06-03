"use client";

// 멀티테넌트 인증 context (2026-06-03 단계 A).
// JWT 는 httpOnly 쿠키라 JS 가 토큰을 직접 못 본다 → 세션 상태는 GET /auth/me 로 복원.
// i18n/context.tsx 의 Context 패턴을 그대로 따름.

import { usePathname, useRouter } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";

import {
  type AuthUser,
  getMe,
  login as apiLogin,
  logout as apiLogout,
  register as apiRegister,
  switchSpace as apiSwitchSpace,
} from "@/lib/api";

interface AuthContextValue {
  user: AuthUser | null;
  loading: boolean; // 초기 /auth/me 확인 중
  login: (email: string, password: string) => Promise<void>;
  register: (
    email: string,
    password: string,
    displayName?: string,
  ) => Promise<void>;
  logout: () => Promise<void>;
  switchSpace: (spaceId: string) => Promise<void>;
  activeSpace: AuthUser["spaces"][number] | null;
}

const AuthContext = createContext<AuthContextValue | null>(null);

// 로그인 없이 접근 가능한 경로 (가드 제외).
const PUBLIC_PATHS = ["/login"];

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);
  const router = useRouter();
  const pathname = usePathname();

  // mount 시 세션 복원.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const u = await getMe();
        if (!cancelled) setUser(u);
      } catch {
        if (!cancelled) setUser(null);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // 가드 — 확인 끝났는데 미인증 + 보호 경로면 /login 으로.
  useEffect(() => {
    if (loading) return;
    if (!user && !PUBLIC_PATHS.includes(pathname)) {
      router.replace("/login");
    }
  }, [loading, user, pathname, router]);

  const login = useCallback(async (email: string, password: string) => {
    const u = await apiLogin(email, password);
    setUser(u);
  }, []);

  const register = useCallback(
    async (email: string, password: string, displayName?: string) => {
      const u = await apiRegister(email, password, displayName);
      setUser(u);
    },
    [],
  );

  const logout = useCallback(async () => {
    try {
      await apiLogout();
    } finally {
      setUser(null);
      router.replace("/login");
    }
  }, [router]);

  const switchSpace = useCallback(async (spaceId: string) => {
    const u = await apiSwitchSpace(spaceId);
    setUser(u);
    // 활성 space 가 바뀌면 데이터가 전부 바뀌므로 새로고침으로 모든 뷰 재로딩.
    if (typeof window !== "undefined") window.location.reload();
  }, []);

  const activeSpace =
    user?.spaces.find((s) => s.id === user.active_space_id) ?? null;

  return (
    <AuthContext.Provider
      value={{ user, loading, login, register, logout, switchSpace, activeSpace }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth 는 AuthProvider 안에서만 사용 가능합니다");
  }
  return ctx;
}
