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

export interface AskMessage {
  role: "user" | "assistant";
  content: string;
  ts: string;
  citations?: AskCitation[];
  related_wikis?: AskRelatedWiki[];
  llm_model?: string;
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

const KEY = "linkmind:ask:store:v1";
const EMPTY: AskStoreData = { sessions: [], projects: [], activeSessionId: null };

/** 짧은 고유 id — 브라우저 crypto.randomUUID 우선, 없으면 time+rand fallback. */
export function genId(prefix: string): string {
  try {
    return `${prefix}_${crypto.randomUUID().slice(0, 8)}`;
  } catch {
    return `${prefix}_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
  }
}

export function loadStore(): AskStoreData {
  if (typeof window === "undefined") return EMPTY;
  try {
    const raw = localStorage.getItem(KEY);
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

export function saveStore(data: AskStoreData): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(KEY, JSON.stringify(data));
  } catch {
    // quota 초과 등 — MVP 단계에선 무시 (백엔드 이전 시 해소)
  }
}

/** 세션 제목 — 없으면 첫 사용자 메시지 앞부분으로. */
export function sessionTitle(s: AskSession): string {
  if (s.title) return s.title;
  const firstUser = s.messages.find((m) => m.role === "user");
  return firstUser?.content?.slice(0, 60) || "새 대화";
}
