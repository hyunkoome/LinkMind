/**
 * /ask 대화 세션 + 프로젝트 localStorage 영속 저장 (2026-06-02).
 *
 * 현재 ask 는 1-shot RAG 이고 메시지가 메모리에만 있어 새로고침하면 사라졌다.
 * 이 모듈이 세션(대화)과 프로젝트를 브라우저 localStorage 에 저장해 유지한다.
 *
 * MVP — self-host 개인 도구라 단일 브라우저 영속으로 충분 (§6). 멀티턴 백엔드 +
 * 학습 파이프라인(Phase 4) 도입 시 DB 로 마이그레이션 예정. 그때 export 한
 * JSON 을 backend 세션 테이블에 적재하면 됨 (구조 동일하게 설계).
 */

import type { AskCitation, AskRelatedWiki } from "./api";
import { exportAskStore, syncAskStore } from "./api";

export interface IngestedSource {
  item_id: string;
  title: string;
  url: string;
  created: boolean; // false = 이미 수집돼 있던 자료 (idempotent)
}

export interface AskMessage {
  role: "user" | "assistant";
  content: string;
  ts: string;
  citations?: AskCitation[];
  related_wikis?: AskRelatedWiki[];
  llm_model?: string;
  // URL-paste-ingest 로 이 답변 직전 수집한 자료 (assistant 메시지에 표시)
  ingested?: IngestedSource[];
}

export interface AskSession {
  id: string;
  title: string;
  projectId: string | null;
  createdAt: number;
  updatedAt: number;
  messages: AskMessage[];
}

export interface AskProject {
  id: string;
  name: string;
  createdAt: number;
}

export interface AskStoreData {
  sessions: AskSession[];
  projects: AskProject[];
  activeSessionId: string | null;
}

// 계정별 키 — localStorage 가 계정/브라우저에 안 묶이던 문제(프라이버시) 해결.
// 서버(PUT /sessions/sync, GET /sessions/export)가 진실 소스, localStorage 는 캐시.
const KEY_PREFIX = "linkmind:ask:store:v1";
const LEGACY_KEY = "linkmind:ask:store:v1"; // 옛 계정-무관 전역 키 (로드 시 정리)
const EMPTY: AskStoreData = { sessions: [], projects: [], activeSessionId: null };

function keyFor(userId: string): string {
  return `${KEY_PREFIX}:${userId}`;
}

/** 짧은 고유 id — 브라우저 crypto.randomUUID 우선, 없으면 time+rand fallback. */
export function genId(prefix: string): string {
  try {
    return `${prefix}_${crypto.randomUUID().slice(0, 8)}`;
  } catch {
    return `${prefix}_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
  }
}

// 계정별 localStorage 캐시 로드 (서버 미가용 시 fallback).
export function loadStore(userId: string): AskStoreData {
  if (typeof window === "undefined") return EMPTY;
  try {
    const raw = localStorage.getItem(keyFor(userId));
    if (!raw) return EMPTY;
    const data = JSON.parse(raw) as Partial<AskStoreData>;
    return {
      sessions: Array.isArray(data.sessions) ? data.sessions : [],
      projects: Array.isArray(data.projects) ? data.projects : [],
      activeSessionId: data.activeSessionId ?? null,
    };
  } catch {
    return EMPTY;
  }
}

// 계정별 캐시 저장 + 서버 미러(write-through, fire-and-forget).
export function saveStore(userId: string, data: AskStoreData): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(keyFor(userId), JSON.stringify(data));
  } catch {
    // quota 초과 등 무시 — 서버 미러가 진실 소스.
  }
  // 세션/프로젝트만 서버에 미러 (activeSessionId 는 로컬 UI 상태).
  void syncAskStore(data.projects, data.sessions).catch(() => {
    /* 오프라인 등 — 다음 변경 때 재시도 */
  });
}

// 로그인 시 서버에서 본인 대화 로드 → 계정별 캐시 덮어쓰기. 서버가 진실 소스라
// 다른 브라우저/다른 계정에서도 정확히 본인 대화만 보인다. 서버 실패 시 캐시 fallback.
export async function loadStoreFromServer(userId: string): Promise<AskStoreData> {
  // 옛 계정-무관 전역 키 정리 (프라이버시 — 다른 계정 대화가 남지 않게).
  if (typeof window !== "undefined") {
    try {
      localStorage.removeItem(LEGACY_KEY);
    } catch {
      /* ignore */
    }
  }
  try {
    const ex = await exportAskStore();
    const data: AskStoreData = {
      projects: (Array.isArray(ex.projects) ? ex.projects : []) as AskProject[],
      sessions: (Array.isArray(ex.sessions) ? ex.sessions : []) as AskSession[],
      activeSessionId: null,
    };
    if (typeof window !== "undefined") {
      try {
        localStorage.setItem(keyFor(userId), JSON.stringify(data));
      } catch {
        /* ignore */
      }
    }
    return data;
  } catch {
    return loadStore(userId);
  }
}

/** 세션 제목 — 없으면 첫 사용자 메시지 앞부분으로. */
export function sessionTitle(s: AskSession): string {
  if (s.title) return s.title;
  const firstUser = s.messages.find((m) => m.role === "user");
  return firstUser?.content?.slice(0, 60) || "새 대화";
}
