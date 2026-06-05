"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import WikiDetailView from "@/components/wiki/WikiDetailView";
import {
  addArxivKeyword,
  clearArxivGroup,
  collectArxiv,
  deleteArxivKeyword,
  getArxivCategories,
  getArxivFeed,
  getUiPrefs,
  getWikiByItem,
  listArxivKeywords,
  renameArxivGroup,
  searchArxiv,
  setUiPref,
  toggleArxivKeyword,
  type ArxivCategory,
  type ArxivPaper,
  type CollectionKeyword,
} from "@/lib/api";
import { useAuth } from "@/lib/auth/context";
import { useT } from "@/lib/i18n/context";

const LS_LEFT = "arxiv:leftW";
const LS_RIGHT = "arxiv:rightW";
const LEFT_MIN = 200, LEFT_MAX = 680;
const RIGHT_MIN = 360, RIGHT_MAX = 1600;   // 우측 넓게 = 중간 더 줄일 수 있음
const PAGE_SIZE = 10;
const UNGROUPED = "__ungrouped__";
const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

// arxiv 대분류(archive) → 한글 설명. 약자만 보면 뭔지 모르므로 'cs: 컴퓨터과학' 처럼 표기.
const CAT_KO: Record<string, string> = {
  cs: "컴퓨터과학", math: "수학", eess: "전기전자·시스템", stat: "통계학",
  econ: "경제학", physics: "물리학(일반)", "astro-ph": "천체물리",
  "cond-mat": "응집물질물리", "gr-qc": "일반상대성·양자우주", "hep-ex": "고에너지물리(실험)",
  "hep-lat": "고에너지물리(격자)", "hep-ph": "고에너지물리(현상)", "hep-th": "고에너지물리(이론)",
  "math-ph": "수리물리", nlin: "비선형과학", "nucl-ex": "핵물리(실험)", "nucl-th": "핵물리(이론)",
  "quant-ph": "양자물리", "q-bio": "정량생물학", "q-fin": "정량금융",
  // 구형(legacy) 약자
  "chao-dyn": "카오스동역학", "q-alg": "양자대수", "alg-geom": "대수기하", "dg-ga": "미분기하",
  "funct-an": "함수해석", "adap-org": "적응·자기조직화", "comp-gas": "격자기체",
  "patt-sol": "패턴·솔리톤", "solv-int": "가적분계", "mtrl-th": "재료이론", "supr-con": "초전도",
  "plasm-ph": "플라즈마물리", "atom-ph": "원자물리", "chem-ph": "화학물리", "cmp-lg": "전산언어학",
  "bayes-an": "베이즈분석", "ao-sci": "대기해양과학", "acc-phys": "가속기물리",
};

function Divider({ onPointerDown }: { onPointerDown: (e: React.PointerEvent) => void }) {
  return (
    <div
      role="separator"
      onPointerDown={onPointerDown}
      className="shrink-0 w-1.5 cursor-col-resize bg-zinc-200 dark:bg-zinc-800 hover:bg-orange-400 dark:hover:bg-orange-500 transition-colors"
    />
  );
}

export default function ArxivAdminPage() {
  const { locale } = useT();
  const { activeSpace } = useAuth();
  const isAdmin = activeSpace?.role === "owner" || activeSpace?.role === "admin";
  const ko = locale === "ko";

  // ── 패널 폭 ──
  const [leftW, setLeftW] = useState(300);
  const [rightW, setRightW] = useState(640);
  const [hydrated, setHydrated] = useState(false);   // localStorage 읽기 전 저장 방지

  // ── 키워드/그룹 ──
  const [keywords, setKeywords] = useState<CollectionKeyword[]>([]);
  const [kwError, setKwError] = useState<string | null>(null);
  const [newGroup, setNewGroup] = useState("");
  const [localGroups, setLocalGroups] = useState<string[]>([]);      // 키워드 없는 빈 그룹(로컬)
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<Set<string>>(new Set());  // 다중선택 그룹
  const [groupKwInput, setGroupKwInput] = useState<Record<string, string>>({});
  const [renaming, setRenaming] = useState<Record<string, string>>({}); // group→편집중 새이름

  // ── 중간 리스트 ──
  const [list, setList] = useState<ArxivPaper[]>([]);
  const [listMode, setListMode] = useState<"feed" | "search">("feed");
  const [searchLabel, setSearchLabel] = useState("");   // 표시용 라벨(그룹 이름 등)
  const [searchKws, setSearchKws] = useState<string[]>([]);  // 실제 검색 키워드(재검색용)
  const [activeKeywords, setActiveKeywords] = useState(0);
  const [listLoading, setListLoading] = useState(false);
  const [listError, setListError] = useState<string | null>(null);
  const [collectingIds, setCollectingIds] = useState<Set<string>>(new Set());  // 동시 수집 가능 — 누른 버튼만 처리표시
  const [batchCollecting, setBatchCollecting] = useState(false);
  const [refine, setRefine] = useState("");   // 결과 내 검색(세분화)
  const [page, setPage] = useState(0);
  const [total, setTotal] = useState(0);   // 전체 매칭 수(서버 페이지네이션)
  // 위키 유/무 필터 — 기본 'has'(위키 유). ref 로 현재값 공유(모든 재요청 경로가 참조).
  const [wikiFilter, setWikiFilter] = useState<"all" | "has" | "none">("has");
  const wikiRef = useRef<"all" | "has" | "none">("has");
  useEffect(() => { wikiRef.current = wikiFilter; }, [wikiFilter]);

  // ── 카테고리 필터 ──
  const [cats, setCats] = useState<ArxivCategory[]>([]);
  const [selectedCats, setSelectedCats] = useState<Set<string>>(new Set());
  const [catOpen, setCatOpen] = useState(false);
  const CATS_PREF = "arxiv:cats";

  // ── 우측 패널: 위키 or arXiv PDF ──
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null);
  const [openingWiki, setOpeningWiki] = useState<string | null>(null);
  const [pdfView, setPdfView] = useState<{ url: string; title: string } | null>(null);  // arXiv PDF 우측 표시

  // 패널 폭 — DB(유저별) 우선, 없으면 localStorage. 마운트 시 로드.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      let l = Number(localStorage.getItem(LS_LEFT));
      let r = Number(localStorage.getItem(LS_RIGHT));
      try {
        const { prefs } = await getUiPrefs();
        if (prefs[LS_LEFT]) l = Number(prefs[LS_LEFT]);
        if (prefs[LS_RIGHT]) r = Number(prefs[LS_RIGHT]);
      } catch { /* DB 실패 시 localStorage */ }
      if (cancelled) return;
      if (l) setLeftW(clamp(l, LEFT_MIN, LEFT_MAX));
      if (r) setRightW(clamp(r, RIGHT_MIN, RIGHT_MAX));
      setHydrated(true);
    })();
    return () => { cancelled = true; };
  }, []);

  // 드래그 끝에만 저장 (localStorage 즉시 + DB)
  const persistWidth = (key: string, value: number) => {
    localStorage.setItem(key, String(value));
    void setUiPref(key, String(value)).catch(() => {});
  };

  const startDrag = (side: "left" | "right", e: React.PointerEvent) => {
    e.preventDefault();
    const startX = e.clientX, startLeft = leftW, startRight = rightW;
    let finalLeft = startLeft, finalRight = startRight;
    document.body.style.userSelect = "none";
    document.body.style.cursor = "col-resize";
    const onMove = (ev: PointerEvent) => {
      const dx = ev.clientX - startX;
      if (side === "left") { finalLeft = clamp(startLeft + dx, LEFT_MIN, LEFT_MAX); setLeftW(finalLeft); }
      else { finalRight = clamp(startRight - dx, RIGHT_MIN, RIGHT_MAX); setRightW(finalRight); }
    };
    const onUp = () => {
      document.body.style.userSelect = "";
      document.body.style.cursor = "";
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      if (side === "left") persistWidth(LS_LEFT, finalLeft);
      else persistWidth(LS_RIGHT, finalRight);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };

  const loadKeywords = useCallback(async () => {
    try {
      const r = await listArxivKeywords();
      setKeywords(r.keywords);
    } catch (e) { setKwError((e as Error).message); }
  }, []);

  const loadFeed = useCallback(async (catsArg?: string[], pg = 0, refineArg = "") => {
    setListLoading(true); setListError(null); setListMode("feed"); setPage(pg);
    try {
      const r = await getArxivFeed(PAGE_SIZE, catsArg, pg * PAGE_SIZE, refineArg, wikiRef.current);
      setList(r.papers); setTotal(r.total); setActiveKeywords(r.keywords);
      if (r.total === 0) setListError(ko ? "매칭된 논문이 없습니다." : "No matches yet.");
    } catch (e) { setListError((e as Error).message); }
    finally { setListLoading(false); }
  }, [ko]);

  // 카테고리 목록 + 저장된 선택 복원
  useEffect(() => {
    if (!isAdmin) return;
    void getArxivCategories().then((r) => setCats(r.categories)).catch(() => {});
    void getUiPrefs().then(({ prefs }) => {
      if (prefs[CATS_PREF]) {
        try { setSelectedCats(new Set(JSON.parse(prefs[CATS_PREF]))); } catch { /* ignore */ }
      }
    }).catch(() => {});
  }, [isAdmin]);

  // 마운트 기본값: 등록 키워드 로드 → **모든 대표 그룹 선택 + 그 키워드로 검색**
  // (피드가 아니라 '전 그룹 검색'이 기본 화면). 그룹이 없으면 피드로 폴백.
  useEffect(() => {
    if (!isAdmin) return;
    (async () => {
      const r = await listArxivKeywords();
      setKeywords(r.keywords);
      const realGroups = new Set<string>();
      for (const k of r.keywords) if (k.group_label) realGroups.add(k.group_label);
      if (realGroups.size === 0) { void loadFeed(catArr()); return; }
      setSelected(realGroups);
      const kws = r.keywords.filter((k) => k.enabled && k.group_label).map((k) => k.keyword);
      if (kws.length) runSearch(kws, Array.from(realGroups).join(" + "));
      else void loadFeed(catArr());
    })().catch((e) => setKwError((e as Error).message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isAdmin]);

  const catArr = () => Array.from(selectedCats);

  const runSearch = async (keywordsArg: string[], label: string, pg = 0, refineArg = "") => {
    const kws = keywordsArg.map((s) => s.trim()).filter(Boolean);
    if (kws.length === 0) return;
    setListLoading(true); setListError(null); setListMode("search");
    setSearchLabel(label); setSearchKws(kws); setPage(pg);
    const cp = catArr();
    const off = pg * PAGE_SIZE;
    try {
      const r = kws.length > 1
        ? await searchArxiv({ keywords: kws, max_results: PAGE_SIZE, offset: off, category_prefixes: cp, refine: refineArg, wiki_filter: wikiRef.current })
        : await searchArxiv({ query: kws[0], max_results: PAGE_SIZE, offset: off, category_prefixes: cp, refine: refineArg, wiki_filter: wikiRef.current });
      setList(r.papers); setTotal(r.total);
      if (r.total === 0) setListError(ko ? "검색 결과가 없습니다." : "No results.");
    } catch (e) { setListError((e as Error).message); }
    finally { setListLoading(false); }
  };

  // 페이지 이동 → 현재 모드(피드/검색)로 그 페이지 재요청 (refine 유지)
  const gotoPage = (pg: number) => {
    if (pg < 0) return;
    if (listMode === "feed") void loadFeed(catArr(), pg, refine);
    else void runSearch(searchKws, searchLabel, pg, refine);
  };

  // 결과 내 검색(refine) 적용 — 현재 모드 첫 페이지부터 재요청
  const applyRefine = () => {
    if (listMode === "feed") void loadFeed(catArr(), 0, refine);
    else void runSearch(searchKws, searchLabel, 0, refine);
  };
  const clearRefine = () => {
    setRefine("");
    setTimeout(() => listMode === "feed" ? loadFeed(catArr(), 0, "") : runSearch(searchKws, searchLabel, 0, ""), 0);
  };

  // 위키 유/무 필터 적용 — 현재 모드 첫 페이지부터 재요청(refine 유지)
  const applyWiki = (next: "all" | "has" | "none") => {
    setWikiFilter(next);
    wikiRef.current = next;
    setTimeout(() => listMode === "feed" ? loadFeed(catArr(), 0, refine) : runSearch(searchKws, searchLabel, 0, refine), 0);
  };

  // 위키 생성 중(수집됨+미완료) 항목이 있으면 7s 간격으로 조용히 재요청 → 완료 시 자동 '위키' 전환.
  // spinner 안 띄우고 list/total 만 갱신. pending 이 사라지면 자동 종료(list 변화로 재평가).
  useEffect(() => {
    if (!list.some((p) => p.collected && p.wiki_status !== "completed")) return;
    const off = page * PAGE_SIZE;
    const cp = Array.from(selectedCats);
    const t = setTimeout(async () => {
      try {
        const r = listMode === "feed"
          ? await getArxivFeed(PAGE_SIZE, cp, off, refine, wikiRef.current)
          : searchKws.length > 1
            ? await searchArxiv({ keywords: searchKws, max_results: PAGE_SIZE, offset: off, category_prefixes: cp, refine, wiki_filter: wikiRef.current })
            : await searchArxiv({ query: searchKws[0], max_results: PAGE_SIZE, offset: off, category_prefixes: cp, refine, wiki_filter: wikiRef.current });
        setList(r.papers); setTotal(r.total);
      } catch { /* 폴링 실패 무시 */ }
    }, 7000);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [list, listMode, page, refine, searchKws, selectedCats]);

  // 선택 카테고리 적용 (저장 + 현재 리스트 갱신)
  const applyCats = (next: Set<string>) => {
    setSelectedCats(next);
    void setUiPref(CATS_PREF, JSON.stringify(Array.from(next))).catch(() => {});
    setTimeout(() => {
      if (listMode === "feed") void loadFeed(Array.from(next));
      else void runSearch(searchKws, searchLabel);   // 라벨 아닌 실제 키워드로 재검색
    }, 0);
  };
  const toggleCat = (major: string) => {
    const n = new Set(selectedCats);
    n.has(major) ? n.delete(major) : n.add(major);
    applyCats(n);
  };

  // 그룹: DB 키워드 그룹 + 로컬 빈 그룹
  const groups = useMemo(() => {
    const m = new Map<string, CollectionKeyword[]>();
    for (const g of localGroups) m.set(g, []);
    for (const k of keywords) {
      const g = k.group_label || UNGROUPED;
      if (!m.has(g)) m.set(g, []);
      m.get(g)!.push(k);
    }
    // 미분류는 맨 뒤
    return Array.from(m.entries()).sort((a, b) =>
      a[0] === UNGROUPED ? 1 : b[0] === UNGROUPED ? -1 : 0,
    );
  }, [keywords, localGroups]);

  // ── 그룹/키워드 핸들러 ──
  const addGroup = () => {
    const g = newGroup.trim();
    if (!g) return;
    if (!localGroups.includes(g) && !keywords.some((k) => k.group_label === g)) {
      setLocalGroups((p) => [...p, g]);
      setExpanded((p) => new Set(p).add(g));
    }
    setNewGroup("");
  };
  const addKeywordToGroup = async (group: string) => {
    const kw = (groupKwInput[group] || "").trim();
    if (!kw) return;
    try {
      await addArxivKeyword(kw, group === UNGROUPED ? null : group);
      setGroupKwInput((p) => ({ ...p, [group]: "" }));
      setLocalGroups((p) => p.filter((g) => g !== group)); // 이제 DB 그룹
      await loadKeywords();
    } catch (e) { setKwError((e as Error).message); }
  };
  const onRenameGroup = async (oldLabel: string) => {
    const nw = (renaming[oldLabel] || "").trim();
    setRenaming((p) => { const n = { ...p }; delete n[oldLabel]; return n; });
    if (!nw || nw === oldLabel) return;
    try {
      if (localGroups.includes(oldLabel)) {
        setLocalGroups((p) => p.map((g) => (g === oldLabel ? nw : g)));
      } else {
        await renameArxivGroup(oldLabel, nw);
        await loadKeywords();
      }
    } catch (e) { setKwError((e as Error).message); }
  };
  const onDeleteGroup = async (label: string) => {
    if (label === UNGROUPED) return;
    if (!confirm(ko ? `'${label}' 그룹을 삭제할까요? (키워드는 미분류로 이동)` : `Delete group '${label}'?`)) return;
    try {
      if (localGroups.includes(label)) {
        setLocalGroups((p) => p.filter((g) => g !== label));
      } else {
        await clearArxivGroup(label);
        await loadKeywords();
      }
      setSelected((p) => { const n = new Set(p); n.delete(label); return n; });
    } catch (e) { setKwError((e as Error).message); }
  };
  const onDeleteKeyword = async (id: string) => {
    try { await deleteArxivKeyword(id); await loadKeywords(); }
    catch (e) { setKwError((e as Error).message); }
  };
  const onToggleKeyword = async (k: CollectionKeyword) => {
    try { await toggleArxivKeyword(k.id, !k.enabled); await loadKeywords(); }
    catch (e) { setKwError((e as Error).message); }
  };

  // 선택 그룹들의 enabled 키워드 union 으로 검색 (set 인자 = 즉시 반영용).
  // 선택이 비면 피드로 복귀.
  const searchGroups = (sel: Set<string>) => {
    const kws: string[] = [];
    for (const [gname, gkws] of groups) {
      if (!sel.has(gname)) continue;
      for (const k of gkws) if (k.enabled) kws.push(k.keyword);
    }
    if (kws.length === 0) { void loadFeed(catArr()); return; }
    const label = Array.from(sel).filter((g) => g !== UNGROUPED).join(" + ");
    void runSearch(kws, label || (ko ? "선택 그룹" : "selected"));
  };
  // 그룹 체크 토글 → 즉시 그 선택으로 검색
  const toggleGroupSelect = (gname: string) => {
    setSelected((p) => {
      const n = new Set(p);
      n.has(gname) ? n.delete(gname) : n.add(gname);
      setTimeout(() => searchGroups(n), 0);
      return n;
    });
  };

  const refreshList = async () => {
    if (listMode === "feed") await loadFeed(catArr(), 0, refine);
    else await runSearch(searchKws, searchLabel, 0, refine);
  };
  const collectPaper = async (arxivId: string) => {
    if (collectingIds.has(arxivId) || batchCollecting) return;   // 그 항목만 중복 방지
    setCollectingIds((p) => new Set(p).add(arxivId));
    try { await collectArxiv([arxivId]); await refreshList(); }
    catch (e) { setListError((e as Error).message); }
    finally { setCollectingIds((p) => { const n = new Set(p); n.delete(arxivId); return n; }); }
  };

  // 서버 페이지네이션 — list 는 이미 현재 페이지(PAGE_SIZE개)
  const pageItems = list;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const pageUncollected = pageItems.filter((p) => !p.collected);

  const collectBatch = async () => {
    if (batchCollecting || pageUncollected.length === 0) return;
    setBatchCollecting(true);
    try { await collectArxiv(pageUncollected.map((p) => p.arxiv_id)); await refreshList(); }
    catch (e) { setListError((e as Error).message); }
    finally { setBatchCollecting(false); }
  };
  const openWiki = async (itemId: string) => {
    setOpeningWiki(itemId);
    try {
      const w = await getWikiByItem(itemId);
      if (w?.slug) { setPdfView(null); setSelectedSlug(w.slug); }
      else setListError(ko ? "이 자료의 위키를 찾지 못했습니다 (생성 중일 수 있음)." : "Wiki not found.");
    } catch (e) { setListError((e as Error).message); }
    finally { setOpeningWiki(null); }
  };
  // arXiv 버튼 → 원문 PDF 를 우측 패널에 표시 (새 탭 X)
  const openPdf = (pdfUrl: string, title: string) => {
    setSelectedSlug(null);
    setPdfView({ url: pdfUrl, title });
  };

  if (!isAdmin) {
    return (
      <main className="h-full overflow-y-auto p-6 max-w-4xl mx-auto w-full">
        <h1 className="text-xl font-semibold">{ko ? "arXiv" : "arXiv"}</h1>
        <div className="text-sm text-zinc-500 mt-4">{ko ? "조직 관리자만 접근 가능합니다." : "Admins only."}</div>
      </main>
    );
  }

  const groupDisplayName = (g: string) => (g === UNGROUPED ? (ko ? "(미분류)" : "(ungrouped)") : g);

  return (
    <main className="h-full flex overflow-hidden">
      {/* ── 좌: 대표 키워드(그룹) ── */}
      <aside style={{ width: leftW }} className="shrink-0 flex flex-col border-r border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 overflow-y-auto">
        <div className="px-3 py-2 border-b border-zinc-200 dark:border-zinc-800">
          <div className="text-sm font-semibold">{ko ? "🔭 arXiv" : "🔭 arXiv"}</div>
          <div className="text-[11px] text-zinc-500">{ko ? `활성 키워드 ${activeKeywords}개` : `${activeKeywords} active`}</div>
        </div>

        {/* ── 분야(카테고리) 필터 — 다중선택 + 전체 ── */}
        <div className="border-b border-zinc-200 dark:border-zinc-800">
          <button type="button" onClick={() => setCatOpen((v) => !v)}
            className="w-full flex items-center justify-between px-3 py-2 text-xs font-semibold hover:bg-zinc-50 dark:hover:bg-zinc-800/50">
            <span>{ko ? "분야" : "Fields"} <span className="font-normal text-zinc-400">{selectedCats.size === 0 ? (ko ? "(전체)" : "(all)") : `(${selectedCats.size})`}</span></span>
            <span className="text-[10px] text-zinc-400">{catOpen ? "▼" : "▶"}</span>
          </button>
          {catOpen && (
            <div className="max-h-52 overflow-y-auto px-2 pb-2 space-y-0.5">
              <label className="flex items-center gap-2 px-1.5 py-0.5 rounded hover:bg-zinc-100 dark:hover:bg-zinc-800 cursor-pointer text-xs">
                <input type="checkbox" checked={selectedCats.size === 0} onChange={() => applyCats(new Set())} />
                <span className="font-medium">{ko ? "전체" : "All"}</span>
              </label>
              {cats.map((c) => (
                <label key={c.major} className="flex items-center gap-2 px-1.5 py-0.5 rounded hover:bg-zinc-100 dark:hover:bg-zinc-800 cursor-pointer text-xs">
                  <input type="checkbox" checked={selectedCats.has(c.major)} onChange={() => toggleCat(c.major)} />
                  <span className="flex-1 truncate" title={CAT_KO[c.major] || c.major}>
                    <span className="font-medium">{c.major}</span>
                    {ko && CAT_KO[c.major] && <span className="text-zinc-400">: {CAT_KO[c.major]}</span>}
                  </span>
                  <span className="text-[10px] text-zinc-400 shrink-0">{c.count > 9999 ? `${Math.round(c.count / 1000)}k` : c.count}</span>
                </label>
              ))}
            </div>
          )}
        </div>

        {/* 현재 검색 상태 — 분야 아래 */}
        <div className="px-3 py-2 border-b border-zinc-200 dark:border-zinc-800 flex items-center gap-2">
          <span className="text-xs font-bold flex-1 truncate" title={searchLabel}>
            {listMode === "feed" ? (ko ? "📰 최신 논문" : "📰 Latest") : `🔍 ${searchLabel}`}
          </span>
          {listMode === "search" && (
            <button type="button" onClick={() => loadFeed(catArr())} className="text-[11px] text-orange-600 hover:underline whitespace-nowrap">{ko ? "← 피드" : "← Feed"}</button>
          )}
        </div>

        {/* 대표 키워드 추가 */}
        <div className="px-3 py-2 flex gap-1.5 border-b border-zinc-200 dark:border-zinc-800">
          <input type="text" value={newGroup} onChange={(e) => setNewGroup(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && addGroup()}
            placeholder={ko ? "대표 키워드 추가…" : "Add group…"}
            className="flex-1 min-w-0 px-2 py-1 text-xs rounded border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900" />
          <button type="button" onClick={addGroup} className="text-xs px-2 py-1 rounded bg-orange-500 text-white hover:bg-orange-600">+그룹</button>
        </div>
        {kwError && <div className="px-3 py-1 text-[11px] text-rose-600">{kwError}</div>}
        <div className="px-2 py-2 space-y-2">
          {groups.map(([gname, gkws]) => {
            const isOpen = expanded.has(gname);
            const enabledKws = gkws.filter((k) => k.enabled).map((k) => k.keyword);
            const isRenaming = gname in renaming;
            return (
              <div key={gname} className={`rounded border ${selected.has(gname) ? "border-blue-400 dark:border-blue-600" : "border-zinc-200 dark:border-zinc-800"}`}>
                <div className="flex items-center gap-1 px-2 py-1.5 bg-zinc-50 dark:bg-zinc-800/50">
                  {gname !== UNGROUPED && (
                    <input type="checkbox" checked={selected.has(gname)}
                      onChange={() => toggleGroupSelect(gname)}
                      className="shrink-0" title={ko ? "선택하면 바로 검색" : "select & search"} />
                  )}
                  <button type="button" onClick={() => setExpanded((p) => { const n = new Set(p); n.has(gname) ? n.delete(gname) : n.add(gname); return n; })}
                    className="text-[10px] text-zinc-500 w-3 shrink-0">{isOpen ? "▼" : "▶"}</button>
                  {isRenaming ? (
                    <input autoFocus type="text" value={renaming[gname]}
                      onChange={(e) => setRenaming((p) => ({ ...p, [gname]: e.target.value }))}
                      onKeyDown={(e) => { if (e.key === "Enter") onRenameGroup(gname); if (e.key === "Escape") setRenaming((p) => { const n = { ...p }; delete n[gname]; return n; }); }}
                      onBlur={() => onRenameGroup(gname)}
                      className="flex-1 min-w-0 px-1 py-0.5 text-xs rounded border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900" />
                  ) : (
                    <button type="button"
                      onClick={() => runSearch(enabledKws.length ? enabledKws : gkws.map((k) => k.keyword), groupDisplayName(gname))}
                      className="flex-1 text-left text-xs font-semibold hover:text-orange-600 dark:hover:text-orange-400 truncate"
                      title={ko ? "이 그룹으로 검색" : "search"}>
                      {groupDisplayName(gname)}
                    </button>
                  )}
                  <span className="text-[10px] text-zinc-400 shrink-0">{gkws.length}</span>
                  {gname !== UNGROUPED && !isRenaming && (
                    <>
                      <button type="button" onClick={() => setRenaming((p) => ({ ...p, [gname]: gname }))} className="text-[10px] text-zinc-400 hover:text-blue-500 shrink-0" title="이름 수정">✏️</button>
                      <button type="button" onClick={() => onDeleteGroup(gname)} className="text-[11px] text-zinc-400 hover:text-rose-500 shrink-0" title="그룹 삭제">×</button>
                    </>
                  )}
                </div>
                {isOpen && (
                  <div className="px-2 py-1.5 space-y-1.5">
                    <div className="flex flex-wrap gap-1">
                      {gkws.map((k) => (
                        <span key={k.id} className={`inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded border ${
                          k.enabled ? "border-orange-300 dark:border-orange-700 bg-orange-50 dark:bg-orange-900/20" : "border-zinc-300 dark:border-zinc-700 text-zinc-400"}`}>
                          <button type="button" onClick={() => onToggleKeyword(k)} className="text-[8px]" title="toggle">{k.enabled ? "●" : "○"}</button>
                          <button type="button" onClick={() => runSearch([k.keyword], k.keyword)} className="hover:underline">{k.keyword}</button>
                          <button type="button" onClick={() => onDeleteKeyword(k.id)} className="text-zinc-400 hover:text-rose-500" title="삭제">×</button>
                        </span>
                      ))}
                      {gkws.length === 0 && <span className="text-[10px] text-zinc-400">{ko ? "(키워드 없음)" : "(empty)"}</span>}
                    </div>
                    {/* 그룹에 키워드 추가 */}
                    <div className="flex gap-1">
                      <input type="text" value={groupKwInput[gname] || ""}
                        onChange={(e) => setGroupKwInput((p) => ({ ...p, [gname]: e.target.value }))}
                        onKeyDown={(e) => e.key === "Enter" && addKeywordToGroup(gname)}
                        placeholder={ko ? "이 그룹에 키워드 추가" : "add keyword"}
                        className="flex-1 min-w-0 px-1.5 py-0.5 text-[10px] rounded border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900" />
                      <button type="button" onClick={() => addKeywordToGroup(gname)} className="text-[10px] px-1.5 py-0.5 rounded bg-orange-500 text-white">+</button>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>

        {/* 결과 내 검색(refine) — 그룹 리스트 아래 */}
        <div className="px-3 py-2 border-t border-zinc-200 dark:border-zinc-800 shrink-0">
          <div className="text-xs font-bold mb-1">{ko ? "결과 내 검색 (, 로 세분화)" : "Refine (comma)"}</div>
          <div className="flex gap-1">
            <input type="text" value={refine} onChange={(e) => setRefine(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && applyRefine()}
              placeholder={ko ? "예: lidar, deep learning" : "e.g. lidar, deep learning"}
              className={`flex-1 min-w-0 px-2 py-1 text-xs rounded border bg-white dark:bg-zinc-900 ${refine.trim() ? "border-blue-400" : "border-zinc-300 dark:border-zinc-700"}`} />
            <button type="button" onClick={applyRefine}
              className="text-xs px-2 py-1 rounded bg-blue-600 text-white hover:bg-blue-700 whitespace-nowrap">{ko ? "검색" : "Go"}</button>
          </div>
          {refine.trim() && (
            <button type="button" onClick={clearRefine}
              className="mt-1 w-full text-[11px] px-2 py-1 rounded border border-zinc-300 dark:border-zinc-700 text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800">
              {ko ? "내부검색 리셋" : "Reset refine"}
            </button>
          )}
        </div>

        {/* 위키 유/무 필터 — 좌측 맨 아래, 라디오 버튼 */}
        <div className="px-3 py-2 border-t border-zinc-200 dark:border-zinc-800 shrink-0">
          <div className="text-xs font-bold mb-1">{ko ? "위키" : "Wiki"}</div>
          <div className="flex items-center gap-4">
            {([["all", ko ? "전체" : "All"], ["has", ko ? "유" : "Has"], ["none", ko ? "무" : "None"]] as const).map(([val, label]) => (
              <label key={val} className="flex items-center gap-1.5 text-xs cursor-pointer">
                <input type="radio" name="wikiFilter" checked={wikiFilter === val} onChange={() => applyWiki(val)}
                  className="w-3.5 h-3.5 accent-blue-600 cursor-pointer" />
                <span className={wikiFilter === val ? "font-semibold text-blue-600 dark:text-blue-400" : "text-zinc-600 dark:text-zinc-300"}>{label}</span>
              </label>
            ))}
          </div>
        </div>

      </aside>

      <Divider onPointerDown={(e) => startDrag("left", e)} />

      {/* ── 중: 논문 리스트 ── */}
      <section className="flex-1 min-w-0 flex flex-col bg-white dark:bg-zinc-900 overflow-hidden">
        <div className="px-3 py-2 border-b border-zinc-200 dark:border-zinc-800 flex items-center gap-2">
          <div className="text-sm font-semibold flex-1 truncate" title={searchLabel}>
            {listMode === "feed" ? (ko ? "📰 최신 논문" : "📰 Latest") : `🔍 ${searchLabel}`}
            {total > 0 && <span className="ml-2 text-[11px] font-normal text-zinc-400">{ko ? `${total.toLocaleString()}편` : total.toLocaleString()}</span>}
          </div>
          <button type="button" onClick={collectBatch} disabled={batchCollecting || pageUncollected.length === 0}
            className="text-xs px-2 py-1 rounded bg-amber-700 text-white hover:bg-amber-800 disabled:opacity-40 whitespace-nowrap"
            title={ko ? "이 페이지 미수집 일괄 수집" : "collect page"}>
            {batchCollecting ? "…" : ko ? `📥 일괄 수집(${pageUncollected.length})` : `📥 Collect(${pageUncollected.length})`}
          </button>
        </div>
        {listError && <div className="px-3 py-2 text-xs text-zinc-400">{listError}</div>}
        <ul className="flex-1 overflow-y-auto px-3 py-2 space-y-1.5">
          {pageItems.map((p) => (
            <li key={p.arxiv_id} className="text-sm border border-zinc-200 dark:border-zinc-800 rounded p-2">
              <div className="font-medium text-zinc-800 dark:text-zinc-100">{p.title}</div>
              <div className="flex items-center flex-wrap gap-x-2 gap-y-1 mt-1">
                <span className="text-[11px] text-zinc-500">
                  {p.arxiv_id}{p.published && ` · ${p.published.slice(0, 10)}`}
                  {p.categories.length > 0 && ` · ${p.categories.slice(0, 3).join(", ")}`}
                </span>
                {/* arXiv (연한 적색) — 원문 PDF 를 우측 패널에 */}
                <button type="button" onClick={() => openPdf(p.pdf_url, p.title)}
                  className="text-[11px] px-2 py-0.5 rounded bg-red-100 hover:bg-red-200 text-zinc-800 border border-red-200">🔭 arXiv</button>
                {!p.collected ? (
                  /* 미수집 — 수집/ingest (연한 갈색) */
                  <button type="button" onClick={() => collectPaper(p.arxiv_id)} disabled={collectingIds.has(p.arxiv_id) || batchCollecting}
                    className="text-[11px] px-2 py-0.5 rounded bg-amber-100 hover:bg-amber-200 text-zinc-800 border border-amber-200 disabled:opacity-40">
                    {collectingIds.has(p.arxiv_id) ? "…" : ko ? "📥 수집" : "📥 Ingest"}
                  </button>
                ) : p.wiki_status === "completed" && p.item_id ? (
                  /* 위키 완료 — wiki (연한 파란색) */
                  <button type="button" onClick={() => openWiki(p.item_id as string)} disabled={openingWiki !== null}
                    className="text-[11px] px-2 py-0.5 rounded bg-blue-100 hover:bg-blue-200 text-zinc-800 border border-blue-200 disabled:opacity-50">
                    {openingWiki === p.item_id ? "…" : ko ? "📖 위키" : "📖 Wiki"}
                  </button>
                ) : (
                  /* 수집됨 + 위키 생성 중 — 비활성 */
                  <button type="button" disabled
                    className="text-[11px] px-2 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-zinc-500 border border-zinc-200 dark:border-zinc-700 cursor-default">
                    {ko ? "⏳ 위키 생성 중" : "⏳ generating"}
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
        {total > 0 && (
          <div className="shrink-0 px-3 py-2 border-t border-zinc-200 dark:border-zinc-800 flex items-center justify-center gap-3 text-xs">
            <button type="button" onClick={() => gotoPage(page - 1)} disabled={page === 0 || listLoading}
              className="px-2 py-1 rounded border border-zinc-300 dark:border-zinc-700 disabled:opacity-40 hover:bg-zinc-100 dark:hover:bg-zinc-800">{ko ? "← 이전" : "← Prev"}</button>
            <span className="text-zinc-500">{page + 1} / {totalPages} <span className="text-zinc-400">({ko ? `전체 ${total.toLocaleString()}편` : total.toLocaleString()})</span></span>
            <button type="button" onClick={() => gotoPage(page + 1)} disabled={page >= totalPages - 1 || listLoading}
              className="px-2 py-1 rounded border border-zinc-300 dark:border-zinc-700 disabled:opacity-40 hover:bg-zinc-100 dark:hover:bg-zinc-800">{ko ? "다음 →" : "Next →"}</button>
          </div>
        )}
      </section>

      <Divider onPointerDown={(e) => startDrag("right", e)} />

      {/* ── 우: 위키 or arXiv PDF ── */}
      <aside style={{ width: rightW }} className="shrink-0 flex flex-col bg-white dark:bg-zinc-900 overflow-hidden">
        {pdfView ? (
          <>
            <div className="shrink-0 px-3 py-2 border-b border-zinc-200 dark:border-zinc-800 flex items-center gap-2">
              <span className="text-xs font-semibold flex-1 truncate" title={pdfView.title}>🔭 {pdfView.title}</span>
              <a href={pdfView.url} target="_blank" rel="noreferrer" className="text-[11px] text-red-600 hover:underline whitespace-nowrap">{ko ? "새 탭" : "New tab"}</a>
              <button type="button" onClick={() => setPdfView(null)} className="text-[11px] text-zinc-400 hover:text-rose-500 whitespace-nowrap">✕</button>
            </div>
            <iframe src={pdfView.url} title={pdfView.title} className="flex-1 w-full border-0" />
          </>
        ) : selectedSlug ? (
          <div className="flex-1 overflow-y-auto">
            <WikiDetailView slug={selectedSlug} variant="panel" onClose={() => setSelectedSlug(null)} onDeleted={() => setSelectedSlug(null)} />
          </div>
        ) : (
          <div className="flex-1 flex items-center justify-center text-sm text-zinc-400 p-6 text-center">
            {ko ? "🔭 arXiv / 📖 위키 버튼을 누르면 여기에 표시됩니다." : "Click 🔭 arXiv / 📖 Wiki to view here."}
          </div>
        )}
      </aside>
    </main>
  );
}
