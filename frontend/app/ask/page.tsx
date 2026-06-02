"use client";

/**
 * /ask — D11 대화형 RAG (2026-05-27 → 2026-06-02 세션/프로젝트 영속화).
 *
 * 3-panel (왼쪽 ↔ 중간 ↔ 오른쪽, 경계 마우스 드래그 리사이즈):
 *  - 왼쪽  (resizable, 기본 240px): ChatGPT 풍 사이드바 — 프로젝트 + 최근 대화
 *  - 중간  (flex-1):                ask 프롬프트 + LLM 답변 + citation + related wikis
 *  - 오른쪽 (resizable, 기본 600px): 선택한 wiki detail (사이드 inline view)
 *
 * 대화 세션 + 프로젝트는 localStorage 영속 (lib/askStore) — 새로고침해도 유지.
 * 외부 라이브러리 없이 pointer 이벤트로 자체 splitter 구현 (§6 MVP).
 *
 * 핵심 비전 (CLAUDE.md §1): 자체 DB + 로컬 LLM 기반 답변, sVLL LoRA 결과 자동 반영.
 */

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import ModelLabel from "@/components/ModelLabel";
import KeywordsEditor from "@/components/wiki/KeywordsEditor";
import WikiBody from "@/components/wiki/WikiBody";
import {
  askQuestion,
  getWikiByItem,
  getWikiPage,
  getWikiStatuses,
  ingestAuto,
  type AskResponse,
  type WikiPageDetail,
} from "@/lib/api";
import {
  genId,
  loadStore,
  saveStore,
  sessionTitle,
  type AskMessage,
  type AskProject,
  type AskSession,
  type IngestedSource,
} from "@/lib/askStore";

// ask 입력 안의 URL 추출 — 붙여넣으면 자동 ingest 후 그 자료를 근거로 답변.
const URL_RE = /https?:\/\/[^\s<>"')\]]+/g;
function extractUrls(text: string): string[] {
  const m = text.match(URL_RE);
  if (!m) return [];
  // trailing 문장부호 제거 + 중복 제거
  const cleaned = m.map((u) => u.replace(/[.,;!?)\]]+$/, ""));
  return Array.from(new Set(cleaned));
}

// 2026-05-27 rename: 'issues' / 'pending' / 'completed' (wiki list 와 일관).
const STATUS_COLORS: Record<string, string> = {
  completed: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400",
  pending: "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400 animate-pulse",
  issues: "bg-rose-100 text-rose-700 dark:bg-rose-900/30 dark:text-rose-400",
};

// ─── 패널 리사이즈 상수 ──────────────────────────────────────────
const LEFT_MIN = 180;
const LEFT_MAX = 440;
const LEFT_DEFAULT = 256;
const RIGHT_MIN = 360;
const RIGHT_MAX = 1100;
const RIGHT_DEFAULT = 600;
const LS_LEFT = "linkmind:ask:leftW";
const LS_RIGHT = "linkmind:ask:rightW";

const clamp = (v: number, min: number, max: number) => Math.min(max, Math.max(min, v));

const SIDEBAR_ITEM =
  "flex items-center gap-2 w-full text-left text-[12px] px-2 py-1.5 rounded-md text-zinc-700 dark:text-zinc-300 hover:bg-zinc-200/60 dark:hover:bg-zinc-800 transition-colors";
const SECTION_LABEL =
  "px-2 mb-1 text-[10px] font-semibold text-zinc-400 dark:text-zinc-500 uppercase tracking-wide";

/** 드래그 가능한 세로 구분선. */
function Divider({ onPointerDown }: { onPointerDown: (e: React.PointerEvent) => void }) {
  return (
    <div
      role="separator"
      aria-orientation="vertical"
      onPointerDown={onPointerDown}
      className="shrink-0 w-1.5 cursor-col-resize bg-zinc-200 dark:bg-zinc-800 hover:bg-orange-400 dark:hover:bg-orange-500 transition-colors"
    />
  );
}

export default function AskPage() {
  const [input, setInput] = useState("");
  const [pending, setPending] = useState(false);
  const [ingestStatus, setIngestStatus] = useState<string | null>(null); // URL 수집 진행 표시
  // URL-paste 후 그 자료의 위키 생성/합성 진행 표시 (pending → completed 폴링)
  const [wikiStatus, setWikiStatus] = useState<{ title: string; slug: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pollTokenRef = useRef(0); // 새 질문/새 대화 시 이전 폴링 취소용
  const sessionsRef = useRef<AskSession[]>([]); // 폴링 클로저에서 최신 sessions 참조

  // ─── 영속 상태 (localStorage) ───
  const [hydrated, setHydrated] = useState(false);
  const [sessions, setSessions] = useState<AskSession[]>([]);
  const [projects, setProjects] = useState<AskProject[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);

  // 사이드바 UI 상태
  const [menuFor, setMenuFor] = useState<string | null>(null); // ⋯ 메뉴 열린 세션 id
  const [newProjInput, setNewProjInput] = useState<string | null>(null); // 프로젝트 인라인 입력
  const [openProjects, setOpenProjects] = useState<Record<string, boolean>>({});

  // 우측 panel — 선택한 wiki
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null);
  const [wikiPage, setWikiPage] = useState<WikiPageDetail | null>(null);
  const [wikiLoading, setWikiLoading] = useState(false);

  // 패널 너비 (px)
  const [leftW, setLeftW] = useState(LEFT_DEFAULT);
  const [rightW, setRightW] = useState(RIGHT_DEFAULT);

  // ─── mount: localStorage 복원 ───
  useEffect(() => {
    const d = loadStore();
    setSessions(d.sessions);
    setProjects(d.projects);
    setActiveId(d.activeSessionId);
    const l = Number(localStorage.getItem(LS_LEFT));
    const r = Number(localStorage.getItem(LS_RIGHT));
    if (l) setLeftW(clamp(l, LEFT_MIN, LEFT_MAX));
    if (r) setRightW(clamp(r, RIGHT_MIN, RIGHT_MAX));
    setHydrated(true);
  }, []);

  // ─── 변경 → 저장 (hydrate 완료 후에만; 첫 commit 의 빈 상태로 덮어쓰기 방지) ───
  useEffect(() => {
    if (!hydrated) return;
    saveStore({ sessions, projects, activeSessionId: activeId });
  }, [hydrated, sessions, projects, activeId]);
  useEffect(() => {
    if (hydrated) localStorage.setItem(LS_LEFT, String(leftW));
  }, [hydrated, leftW]);
  useEffect(() => {
    if (hydrated) localStorage.setItem(LS_RIGHT, String(rightW));
  }, [hydrated, rightW]);

  // 파생: 현재 세션의 메시지
  const activeSession = sessions.find((s) => s.id === activeId) ?? null;
  const messages: AskMessage[] = activeSession?.messages ?? [];

  // 폴링 클로저가 최신 sessions 를 읽을 수 있게 ref 동기화
  useEffect(() => {
    sessionsRef.current = sessions;
  }, [sessions]);

  // 답변 메시지의 related_wikis 배지(body_status 스냅샷)를 live 상태로 갱신.
  // URL-paste 한 위키가 pending→completed 되면 채팅 배지도 따라 바뀌도록.
  const updateWikiBadge = (slug: string, status: string) => {
    setSessions((prev) =>
      prev.map((s) => ({
        ...s,
        messages: s.messages.map((m) =>
          m.related_wikis
            ? {
                ...m,
                related_wikis: m.related_wikis.map((rw) =>
                  rw.slug === slug ? { ...rw, body_status: status } : rw,
                ),
              }
            : m,
        ),
      })),
    );
  };

  // 채팅 auto-scroll
  const chatEndRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [activeId, messages.length, pending]);

  // selectedSlug 변경 → wiki detail fetch
  useEffect(() => {
    if (!selectedSlug) {
      setWikiPage(null);
      return;
    }
    setWikiLoading(true);
    getWikiPage(selectedSlug)
      .then((p) => setWikiPage(p))
      .catch((e) => setError((e as Error).message))
      .finally(() => setWikiLoading(false));
  }, [selectedSlug]);

  // ─── 패널 드래그 ───
  const startDrag = (side: "left" | "right", e: React.PointerEvent) => {
    e.preventDefault();
    const startX = e.clientX;
    const startLeft = leftW;
    const startRight = rightW;
    document.body.style.userSelect = "none";
    document.body.style.cursor = "col-resize";
    const onMove = (ev: PointerEvent) => {
      const dx = ev.clientX - startX;
      if (side === "left") setLeftW(clamp(startLeft + dx, LEFT_MIN, LEFT_MAX));
      else setRightW(clamp(startRight - dx, RIGHT_MIN, RIGHT_MAX));
    };
    const onUp = () => {
      document.body.style.userSelect = "";
      document.body.style.cursor = "";
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };

  // ─── 질문 전송 ───
  // URL-paste 한 자료의 위키를 폴링 — 없으면(classifier 생성 중)·pending(합성 중)이면
  // "생성 중" 표시 유지, completed 되면 우측 패널에 자동 표시. 새 질문/새 대화 시 취소.
  const pollWikiForItem = async (itemId: string) => {
    const token = ++pollTokenRef.current;
    let shownDone = false;
    for (let i = 0; i < 30; i++) {
      if (pollTokenRef.current !== token) return; // 취소됨
      try {
        const w = await getWikiByItem(itemId);
        if (w && pollTokenRef.current === token) {
          updateWikiBadge(w.slug, w.body_status); // 채팅 배지 live 동기화
          if (w.body_status === "completed") {
            setWikiStatus(null);
            if (!shownDone) {
              setSelectedSlug(w.slug); // 완성된 위키를 우측에 1회 표시
              shownDone = true;
            }
            // 답변 메시지(related_wikis)에 이 위키가 들어와 배지까지 갱신됐으면 종료.
            // 아직 답변 전(메시지 X)이면 계속 폴링해 다음 iter 에 배지 반영.
            const inMessage = sessionsRef.current.some((s) =>
              s.messages.some((m) =>
                m.related_wikis?.some((rw) => rw.slug === w.slug),
              ),
            );
            if (inMessage) return;
          } else {
            setWikiStatus({ title: w.title, slug: w.slug }); // 생성/합성 중
          }
        }
      } catch {
        /* 일시 오류 무시하고 계속 폴링 */
      }
      await new Promise((r) => setTimeout(r, 3000));
    }
    if (pollTokenRef.current === token) setWikiStatus(null); // 타임아웃 — 표시 정리
  };

  // 답변의 모든 related_wikis 배지를 live 상태로 — batch 로 현재 status 조회해 갱신.
  // 모두 terminal(completed/issues) 되면 종료. 새 질문/새 대화 시 취소.
  const pollRelatedStatuses = async (slugsIn: string[]) => {
    const slugs = Array.from(new Set(slugsIn));
    if (slugs.length === 0) return;
    const token = pollTokenRef.current; // pollWikiForItem 과 같은 토큰 공유 (공존)
    for (let i = 0; i < 30; i++) {
      if (pollTokenRef.current !== token) return;
      try {
        const statuses = await getWikiStatuses(slugs);
        if (pollTokenRef.current !== token) return;
        for (const [slug, status] of Object.entries(statuses)) {
          updateWikiBadge(slug, status);
        }
        const allTerminal = slugs.every((s) => {
          const st = statuses[s];
          return st === "completed" || st === "issues";
        });
        if (allTerminal) return;
      } catch {
        /* 일시 오류 무시하고 계속 */
      }
      await new Promise((r) => setTimeout(r, 3000));
    }
  };

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const q = input.trim();
    if (!q || pending) return;

    const userMsg: AskMessage = {
      role: "user",
      content: q,
      ts: new Date().toLocaleTimeString("ko-KR"),
    };

    // 활성 세션 없으면 새 세션 생성 (id 를 미리 확정해 비동기 후 append 안전)
    const sid = activeId ?? genId("s");
    if (!activeId) {
      const s: AskSession = {
        id: sid,
        title: q.slice(0, 60),
        projectId: null,
        createdAt: Date.now(),
        updatedAt: Date.now(),
        messages: [userMsg],
      };
      setSessions((prev) => [s, ...prev]);
      setActiveId(sid);
    } else {
      setSessions((prev) =>
        prev.map((s) =>
          s.id === sid
            ? { ...s, messages: [...s.messages, userMsg], updatedAt: Date.now() }
            : s,
        ),
      );
    }

    setInput("");
    setPending(true);
    setError(null);
    setWikiStatus(null);
    pollTokenRef.current++; // 이전 폴링 취소

    try {
      // ── agentic action: URL-paste-ingest ──
      // 입력에 URL 이 있으면 먼저 자동 수집(idempotent) → 그 item 을 답변 context 에 pin.
      // 위키 합성은 ingest 가 건 classifier BackgroundTask 가 백그라운드로 처리.
      const urls = extractUrls(q);
      const pinIds: string[] = [];
      const ingested: IngestedSource[] = [];
      for (let i = 0; i < urls.length; i++) {
        setIngestStatus(`🔗 링크 수집 중 (${i + 1}/${urls.length})…`);
        try {
          const res = await ingestAuto({ url: urls[i], analyze_now: true });
          if (res.item_id) {
            pinIds.push(res.item_id);
            ingested.push({
              item_id: res.item_id,
              title: res.title || urls[i],
              url: urls[i],
              created: !!res.created,
            });
          }
        } catch (ie) {
          setError(`링크 수집 실패: ${urls[i]} — ${(ie as Error).message}`);
        }
      }
      setIngestStatus(null);

      // 수집한 자료의 위키 생성/합성 폴링 시작 (답변 생성과 동시 진행)
      if (pinIds.length) void pollWikiForItem(pinIds[0]);

      const r: AskResponse = await askQuestion({
        question: q,
        top_k: 5,
        pin_item_ids: pinIds.length ? pinIds : undefined,
      });
      const assistantMsg: AskMessage = {
        role: "assistant",
        content: r.answer,
        ts: new Date().toLocaleTimeString("ko-KR"),
        citations: r.citations,
        related_wikis: r.related_wikis,
        llm_model: `${r.llm_provider}/${r.llm_model}`,
        ingested: ingested.length ? ingested : undefined,
      };
      setSessions((prev) =>
        prev.map((s) =>
          s.id === sid
            ? {
                ...s,
                messages: [...s.messages, assistantMsg],
                updatedAt: Date.now(),
                title: s.title || q.slice(0, 60),
              }
            : s,
        ),
      );
      if (r.related_wikis.length > 0 && !selectedSlug) {
        setSelectedSlug(r.related_wikis[0].slug);
      }
      // 모든 관련 위키 배지를 live 상태로 갱신 (pending → completed 자동 반영)
      if (r.related_wikis.length > 0) {
        void pollRelatedStatuses(r.related_wikis.map((w) => w.slug));
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(false);
      setIngestStatus(null);
    }
  };

  // ─── 세션 / 프로젝트 조작 ───
  const newChat = () => {
    setActiveId(null);
    setInput("");
    setError(null);
    setMenuFor(null);
    setWikiStatus(null);
    pollTokenRef.current++; // 진행 중 위키 폴링 취소
  };

  const selectSession = (id: string) => {
    setActiveId(id);
    setMenuFor(null);
    setError(null);
  };

  const createProject = (name: string): AskProject | null => {
    const n = name.trim();
    if (!n) return null;
    const p: AskProject = { id: genId("p"), name: n, createdAt: Date.now() };
    setProjects((prev) => [...prev, p]);
    return p;
  };

  const assignToProject = (sessionId: string, projectId: string | null) => {
    setSessions((prev) =>
      prev.map((s) => (s.id === sessionId ? { ...s, projectId } : s)),
    );
    if (projectId) setOpenProjects((prev) => ({ ...prev, [projectId]: true }));
    setMenuFor(null);
  };

  const newProjectForSession = (sessionId: string) => {
    const name = window.prompt("새 프로젝트 이름");
    if (!name) return;
    const p = createProject(name);
    if (p) assignToProject(sessionId, p.id);
  };

  const renameSession = (id: string) => {
    const s = sessions.find((x) => x.id === id);
    const name = window.prompt("대화 이름", s ? sessionTitle(s) : "");
    if (name === null) return;
    setSessions((prev) =>
      prev.map((x) => (x.id === id ? { ...x, title: name.trim() } : x)),
    );
    setMenuFor(null);
  };

  const deleteSession = (id: string) => {
    if (!window.confirm("이 대화를 삭제할까요?")) return;
    setSessions((prev) => prev.filter((s) => s.id !== id));
    if (activeId === id) setActiveId(null);
    setMenuFor(null);
  };

  const renameProject = (id: string) => {
    const p = projects.find((x) => x.id === id);
    const name = window.prompt("프로젝트 이름", p?.name || "");
    if (!name) return;
    setProjects((prev) => prev.map((x) => (x.id === id ? { ...x, name: name.trim() } : x)));
  };

  const deleteProject = (id: string) => {
    if (!window.confirm("프로젝트를 삭제할까요? (안의 대화는 '최근' 으로 이동)")) return;
    setProjects((prev) => prev.filter((p) => p.id !== id));
    setSessions((prev) => prev.map((s) => (s.projectId === id ? { ...s, projectId: null } : s)));
  };

  // 정렬된 목록
  const recentSessions = sessions
    .filter((s) => !s.projectId)
    .sort((a, b) => b.updatedAt - a.updatedAt);
  const projectSessions = (pid: string) =>
    sessions.filter((s) => s.projectId === pid).sort((a, b) => b.updatedAt - a.updatedAt);

  // ─── 세션 한 줄 (최근/프로젝트 공용) ───
  const renderSessionRow = (s: AskSession) => (
    <div key={s.id} className="relative group">
      <div
        className={`flex items-center gap-1 rounded-md ${
          activeId === s.id ? "bg-zinc-200/70 dark:bg-zinc-800" : "hover:bg-zinc-200/60 dark:hover:bg-zinc-800"
        }`}
      >
        <button
          type="button"
          onClick={() => selectSession(s.id)}
          className="flex-1 min-w-0 flex items-center gap-2 text-left text-[12px] px-2 py-1.5 text-zinc-700 dark:text-zinc-300"
        >
          <span className="w-4 shrink-0 text-center">💬</span>
          <span className="truncate">{sessionTitle(s)}</span>
        </button>
        <button
          type="button"
          onClick={() => setMenuFor(menuFor === s.id ? null : s.id)}
          className="shrink-0 px-1.5 py-1 text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200 opacity-0 group-hover:opacity-100 focus:opacity-100"
          title="옵션"
        >
          ⋯
        </button>
      </div>

      {/* 인라인 옵션 메뉴 (clip 방지 위해 floating 대신 아래로 펼침) */}
      {menuFor === s.id && (
        <div className="mt-0.5 ml-2 mr-1 rounded-md border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 shadow-sm p-1 text-[11px]">
          <div className="px-2 py-1 text-[10px] font-semibold text-zinc-400 uppercase">프로젝트에 추가</div>
          {projects.length > 0 ? (
            projects.map((p) => (
              <button
                key={p.id}
                type="button"
                onClick={() => assignToProject(s.id, p.id)}
                className="flex items-center gap-2 w-full text-left px-2 py-1 rounded hover:bg-zinc-100 dark:hover:bg-zinc-800 text-zinc-700 dark:text-zinc-300"
              >
                <span>📁</span>
                <span className="truncate">{p.name}</span>
                {s.projectId === p.id && <span className="ml-auto text-orange-500">✓</span>}
              </button>
            ))
          ) : (
            <div className="px-2 py-1 text-zinc-400">프로젝트 없음</div>
          )}
          <button
            type="button"
            onClick={() => newProjectForSession(s.id)}
            className="flex items-center gap-2 w-full text-left px-2 py-1 rounded hover:bg-zinc-100 dark:hover:bg-zinc-800 text-zinc-700 dark:text-zinc-300"
          >
            <span>＋</span> 새 프로젝트…
          </button>
          {s.projectId && (
            <button
              type="button"
              onClick={() => assignToProject(s.id, null)}
              className="flex items-center gap-2 w-full text-left px-2 py-1 rounded hover:bg-zinc-100 dark:hover:bg-zinc-800 text-zinc-700 dark:text-zinc-300"
            >
              <span>↩</span> 프로젝트에서 빼기
            </button>
          )}
          <div className="my-1 border-t border-zinc-200 dark:border-zinc-700" />
          <button
            type="button"
            onClick={() => renameSession(s.id)}
            className="flex items-center gap-2 w-full text-left px-2 py-1 rounded hover:bg-zinc-100 dark:hover:bg-zinc-800 text-zinc-700 dark:text-zinc-300"
          >
            <span>✎</span> 이름 변경
          </button>
          <button
            type="button"
            onClick={() => deleteSession(s.id)}
            className="flex items-center gap-2 w-full text-left px-2 py-1 rounded hover:bg-rose-50 dark:hover:bg-rose-900/20 text-rose-600 dark:text-rose-400"
          >
            <span>🗑</span> 삭제
          </button>
        </div>
      )}
    </div>
  );

  return (
    <div className="flex-1 flex overflow-hidden bg-zinc-50 dark:bg-zinc-950">
      {/* 메뉴 열려있을 때 바깥 클릭 닫기 */}
      {menuFor && (
        <div className="fixed inset-0 z-10" onClick={() => setMenuFor(null)} aria-hidden />
      )}

      {/* ───────── 왼쪽: 사이드바 ───────── */}
      <aside
        style={{ width: leftW }}
        className="relative z-20 shrink-0 flex flex-col border-r border-zinc-200 dark:border-zinc-800 bg-zinc-50 dark:bg-zinc-900"
      >
        <div className="shrink-0 px-3 pt-3 pb-2">
          <div className="text-sm font-bold text-orange-600 dark:text-orange-400">🔗 LinkMind</div>
        </div>

        {/* 액션 */}
        <nav className="shrink-0 px-2 space-y-0.5">
          <button type="button" onClick={newChat} className={SIDEBAR_ITEM}>
            <span className="w-4 text-center">＋</span> 새 대화
          </button>
          <Link href="/wiki" className={SIDEBAR_ITEM}>
            <span className="w-4 text-center">📖</span> 위키 라이브러리
          </Link>
        </nav>

        {/* 프로젝트 */}
        <div className="shrink-0 px-2 mt-4">
          <div className="flex items-center justify-between">
            <span className={SECTION_LABEL}>프로젝트</span>
            <button
              type="button"
              onClick={() => setNewProjInput(newProjInput === null ? "" : null)}
              title="새 프로젝트"
              className="mr-1 text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200 text-sm"
            >
              ＋
            </button>
          </div>

          {newProjInput !== null && (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                if (createProject(newProjInput)) setNewProjInput(null);
              }}
              className="px-1 mb-1"
            >
              <input
                autoFocus
                value={newProjInput}
                onChange={(e) => setNewProjInput(e.target.value)}
                onBlur={() => setNewProjInput(null)}
                placeholder="프로젝트 이름…"
                className="w-full text-[12px] px-2 py-1 rounded border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-950 text-zinc-900 dark:text-zinc-100 focus:outline-none focus:border-orange-500"
              />
            </form>
          )}

          {projects.length === 0 && newProjInput === null ? (
            <div className="px-2 py-1 text-[11px] text-zinc-400 dark:text-zinc-600">
              아직 프로젝트가 없습니다
            </div>
          ) : (
            <div className="space-y-0.5">
              {projects.map((p) => {
                const ps = projectSessions(p.id);
                const open = openProjects[p.id];
                return (
                  <div key={p.id}>
                    <div className="flex items-center gap-1 rounded-md hover:bg-zinc-200/60 dark:hover:bg-zinc-800 group/proj">
                      <button
                        type="button"
                        onClick={() => setOpenProjects((prev) => ({ ...prev, [p.id]: !open }))}
                        className="flex-1 min-w-0 flex items-center gap-2 text-left text-[12px] px-2 py-1.5 text-zinc-700 dark:text-zinc-300"
                      >
                        <span className="w-3 shrink-0 text-[9px] text-zinc-400">{open ? "▼" : "▶"}</span>
                        <span>📁</span>
                        <span className="truncate">{p.name}</span>
                        <span className="ml-auto text-[10px] text-zinc-400">{ps.length}</span>
                      </button>
                      <button
                        type="button"
                        onClick={() => renameProject(p.id)}
                        title="이름 변경"
                        className="shrink-0 px-1 text-zinc-400 hover:text-zinc-700 opacity-0 group-hover/proj:opacity-100 text-[11px]"
                      >
                        ✎
                      </button>
                      <button
                        type="button"
                        onClick={() => deleteProject(p.id)}
                        title="프로젝트 삭제"
                        className="shrink-0 px-1 mr-1 text-zinc-400 hover:text-rose-500 opacity-0 group-hover/proj:opacity-100 text-[11px]"
                      >
                        🗑
                      </button>
                    </div>
                    {open && (
                      <div className="ml-3 pl-1 border-l border-zinc-200 dark:border-zinc-800">
                        {ps.length === 0 ? (
                          <div className="px-2 py-1 text-[10px] text-zinc-400">(비어 있음)</div>
                        ) : (
                          ps.map(renderSessionRow)
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* 최근 */}
        <div className="flex-1 min-h-0 overflow-y-auto px-2 mt-4">
          <div className={SECTION_LABEL}>최근</div>
          {recentSessions.length === 0 ? (
            <div className="px-2 py-1 text-[11px] text-zinc-400 dark:text-zinc-600">
              대화 기록이 없습니다
            </div>
          ) : (
            <div className="space-y-0.5">{recentSessions.map(renderSessionRow)}</div>
          )}
        </div>

        {/* 하단 사용자 */}
        <div className="shrink-0 border-t border-zinc-200 dark:border-zinc-800 px-3 py-2.5 flex items-center gap-2">
          <div className="w-6 h-6 shrink-0 rounded-full bg-orange-500 text-white text-[11px] flex items-center justify-center font-semibold">
            L
          </div>
          <div className="min-w-0">
            <div className="text-[11px] font-medium text-zinc-700 dark:text-zinc-300 truncate">LinkMind</div>
            <div className="text-[9px] text-zinc-400 dark:text-zinc-500 truncate">Personal AI Engine</div>
          </div>
        </div>
      </aside>

      <Divider onPointerDown={(e) => startDrag("left", e)} />

      {/* ───────── 중간: ask 채팅 ───────── */}
      <section className="flex-1 min-w-0 flex flex-col bg-white dark:bg-zinc-900">
        <header className="shrink-0 px-4 py-3 border-b border-zinc-200 dark:border-zinc-800">
          <h1 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">🤖 LinkMind Ask</h1>
          <p className="text-[11px] text-zinc-500 dark:text-zinc-400 mt-0.5">
            자체 DB + 로컬 LLM (vLLM <ModelLabel />) 기반 답변. 관련 위키를 클릭하면 우측에 표시.
          </p>
        </header>

        {/* 채팅 history */}
        <div className="flex-1 overflow-y-auto p-4 space-y-3 min-h-0">
          {messages.length === 0 && (
            <div className="text-center py-16 text-xs text-zinc-500">
              <div className="text-3xl mb-2">💭</div>
              질문을 입력하면 LinkMind 가 자체 DB 의 자료로 답합니다.
              <div className="mt-3 text-[10px] text-zinc-400">
                예: &quot;LoRA 가 뭐야?&quot; · &quot;factor graph 어떻게 동작해?&quot;
              </div>
            </div>
          )}

          {messages.map((m, i) => (
            <article
              key={i}
              className={`text-xs rounded p-3 ${
                m.role === "user"
                  ? "bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 max-w-2xl ml-auto"
                  : "bg-zinc-50 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700 max-w-3xl"
              }`}
            >
              <div className="flex items-center justify-between mb-1">
                <span className="font-medium text-zinc-700 dark:text-zinc-300">
                  {m.role === "user" ? "👤 나" : "🤖 LinkMind"}
                </span>
                <span className="text-[10px] text-zinc-400">{m.ts}</span>
              </div>

              {/* URL-paste-ingest 로 이 답변 직전 수집한 자료 chip */}
              {m.ingested && m.ingested.length > 0 && (
                <div className="mb-2 flex flex-wrap gap-1">
                  {m.ingested.map((g) => (
                    <a
                      key={g.item_id}
                      href={g.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      title={g.url}
                      className="inline-flex items-center gap-1 max-w-[260px] text-[10px] px-1.5 py-0.5 rounded-full border border-emerald-300 dark:border-emerald-800 bg-emerald-50 dark:bg-emerald-900/20 text-emerald-700 dark:text-emerald-300 hover:underline"
                    >
                      🔗 {g.created ? "수집됨" : "기존"}
                      <span className="truncate">{g.title}</span>
                    </a>
                  ))}
                </div>
              )}

              {m.role === "assistant" ? (
                <WikiBody body={m.content} className="text-zinc-800 dark:text-zinc-200" />
              ) : (
                <div className="whitespace-pre-wrap text-zinc-800 dark:text-zinc-200 leading-relaxed">
                  {m.content}
                </div>
              )}

              {m.citations && m.citations.length > 0 && (
                <details className="mt-2 text-[10px]">
                  <summary className="cursor-pointer text-zinc-500 hover:text-zinc-700">
                    📎 인용 {m.citations.length}건
                  </summary>
                  <ul className="mt-1 space-y-0.5 pl-3">
                    {m.citations.map((c, j) => (
                      <li key={c.item_id} className="text-zinc-600 dark:text-zinc-400">
                        <span className="text-zinc-400 mr-1">[{j + 1}]</span>
                        {c.source_url ? (
                          <a
                            href={c.source_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-blue-600 dark:text-blue-400 hover:underline"
                          >
                            {c.title || c.item_id.slice(0, 8)}
                          </a>
                        ) : (
                          <span>{c.title || c.item_id.slice(0, 8)}</span>
                        )}
                      </li>
                    ))}
                  </ul>
                </details>
              )}

              {m.related_wikis && m.related_wikis.length > 0 && (
                <div className="mt-2 pt-2 border-t border-zinc-200 dark:border-zinc-700">
                  <div className="text-[10px] text-zinc-500 mb-1">
                    📖 관련 위키 {m.related_wikis.length}건
                  </div>
                  <ul className="space-y-0.5">
                    {m.related_wikis.map((w) => (
                      <li key={w.slug}>
                        <button
                          type="button"
                          onClick={() => setSelectedSlug(w.slug)}
                          className={`w-full text-left text-[11px] px-1.5 py-1 rounded hover:bg-zinc-100 dark:hover:bg-zinc-700 ${
                            selectedSlug === w.slug
                              ? "bg-orange-100 dark:bg-orange-900/30 text-orange-700 dark:text-orange-300"
                              : "text-zinc-700 dark:text-zinc-300"
                          }`}
                        >
                          <span className="font-medium truncate inline-block max-w-[280px] align-middle">
                            {w.title}
                          </span>
                          <span className="ml-1 text-zinc-400 text-[10px]">({w.overlap})</span>
                          <span
                            className={`ml-1 inline-block text-[9px] px-1 rounded ${STATUS_COLORS[w.body_status] || STATUS_COLORS.issues}`}
                          >
                            {w.body_status}
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {m.llm_model && (
                <div className="mt-1.5 text-[9px] text-zinc-400 font-mono">{m.llm_model}</div>
              )}
            </article>
          ))}

          {pending && (
            <div className="text-xs text-zinc-500 italic">
              {ingestStatus
                ? ingestStatus
                : "🤖 LinkMind 가 자체 DB 검색 중… (vLLM ~30-60초)"}
            </div>
          )}

          {/* URL-paste 한 자료의 위키 생성/합성 진행 — 완성되면 우측 패널에 자동 표시 */}
          {wikiStatus && (
            <div className="text-xs text-blue-700 dark:text-blue-300 bg-blue-50 dark:bg-blue-900/20 p-2 rounded border border-blue-200 dark:border-blue-800 flex items-start gap-2">
              <span className="animate-pulse mt-0.5">📝</span>
              <span>
                <span className="font-medium">{wikiStatus.title}</span> 위키 생성 중…
                <Link href="/wiki?status=pending" className="ml-1 underline hover:text-blue-900 dark:hover:text-blue-100">
                  위키 ▸ pending 탭
                </Link>
                에서도 확인할 수 있습니다.
              </span>
            </div>
          )}

          {error && (
            <div className="text-xs text-red-700 dark:text-red-300 bg-red-50 dark:bg-red-900/20 p-2 rounded border border-red-200 dark:border-red-800">
              에러: {error}
            </div>
          )}

          <div ref={chatEndRef} />
        </div>

        {/* 입력 */}
        <form onSubmit={onSubmit} className="shrink-0 p-3 border-t border-zinc-200 dark:border-zinc-800">
          <div className="flex gap-2 max-w-3xl mx-auto">
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="자료에 대해 자연어로 질문…"
              disabled={pending}
              className="flex-1 px-3 py-2 text-sm rounded border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-950 text-zinc-900 dark:text-zinc-100 focus:outline-none focus:border-orange-500 disabled:opacity-50"
            />
            <button
              type="submit"
              disabled={pending || !input.trim()}
              className="px-4 py-2 text-sm rounded bg-orange-500 hover:bg-orange-600 text-white font-medium disabled:opacity-50"
            >
              {pending ? "…" : "전송"}
            </button>
          </div>
        </form>
      </section>

      <Divider onPointerDown={(e) => startDrag("right", e)} />

      {/* ───────── 오른쪽: wiki detail ───────── */}
      <aside
        style={{ width: rightW }}
        className="shrink-0 flex flex-col overflow-hidden bg-zinc-50 dark:bg-zinc-950"
      >
        {!selectedSlug ? (
          <div className="flex-1 flex items-center justify-center text-zinc-400 dark:text-zinc-600">
            <div className="text-center px-4">
              <div className="text-5xl mb-3">📖</div>
              <div className="text-sm">답변의 관련 위키를 클릭하면 여기에 표시됩니다</div>
            </div>
          </div>
        ) : wikiLoading || !wikiPage ? (
          <div className="flex-1 flex items-center justify-center text-zinc-500 text-sm">wiki 로딩 중…</div>
        ) : (
          <div className="flex-1 overflow-y-auto p-5">
            <div className="flex items-center justify-between mb-3">
              <Link
                href={`/wiki/${encodeURIComponent(wikiPage.slug)}`}
                className="text-xs text-orange-600 dark:text-orange-400 hover:underline truncate"
              >
                /wiki/{wikiPage.slug} ↗
              </Link>
              <button
                type="button"
                onClick={() => setSelectedSlug(null)}
                className="shrink-0 ml-2 text-xs text-zinc-500 hover:text-zinc-700"
              >
                ✕ 닫기
              </button>
            </div>

            <article className="bg-white dark:bg-zinc-900 rounded border border-zinc-200 dark:border-zinc-800 p-5">
              <WikiBody body={wikiPage.body || ""} />
            </article>

            <div className="mt-4 grid grid-cols-1 gap-3">
              <section className="bg-white dark:bg-zinc-900 rounded border border-zinc-200 dark:border-zinc-800 p-3">
                <h3 className="text-xs font-semibold mb-1.5">Sources ({wikiPage.sources.length})</h3>
                <ul className="space-y-1">
                  {wikiPage.sources.slice(0, 8).map((s, i) => (
                    <li key={s.item_id} className="text-[11px] text-zinc-700 dark:text-zinc-300">
                      <span className="text-zinc-400 mr-1">[{i + 1}]</span>
                      {s.source_url ? (
                        <a
                          href={s.source_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-blue-600 dark:text-blue-400 hover:underline"
                        >
                          {s.title || s.item_id.slice(0, 8)}
                        </a>
                      ) : (
                        <span>{s.title || s.item_id.slice(0, 8)}</span>
                      )}
                      <span className="ml-1 text-[10px] text-zinc-400">{s.source_type}</span>
                    </li>
                  ))}
                </ul>
              </section>

              <KeywordsEditor
                slug={wikiPage.slug}
                keywords={wikiPage.keywords || []}
                onChange={(next) => setWikiPage({ ...wikiPage, keywords: next })}
              />
            </div>
          </div>
        )}
      </aside>
    </div>
  );
}
