"""
backend.api.admin_arxiv — 키워드 기반 arxiv 수집 (admin 전용, 2026-06-05).

흐름 (manual MVP):
  1. 관심 키워드 등록/관리 (collection_keywords, space 격리)
  2. 키워드/쿼리로 arxiv 검색 미리보기 (arxiv_harvester, DB 저장 X)
  3. 골라 수집 → 각 arxiv_id 를 https://arxiv.org/pdf/{id} 로 ingest_pdf
     → 기존 classifier→wiki_writer_worker daemon 이 논문 위키 자동 합성

핵심 설계:
  - arxiv 검색·필터는 LinkMind 비의존 독립 패키지 `arxiv_harvester` 를 **in-process
    import** 로 사용 (REST 아님 — 같은 프로세스). 나중에 별도 OSS 패키지로 추출.
  - collect 입력은 **arxiv_id 만** 받고 서버가 pdf_url 을 합성한다 (SSRF 방지 — 임의
    URL 을 ingest 시키지 않음). id 형식은 정규식으로 검증.
  - collect 는 순차 처리 (Docling CPU/메모리 부하 + arxiv PDF rate limit). 소수 선택 가정.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import (
    get_current_space_id,
    get_current_user,
    require_space_admin,
)
from backend.api.ingest import UrlIngestRequest, ingest_pdf_endpoint
from backend.db import repository
from backend.db.connection import get_session

logger = logging.getLogger("linkmind.api.admin_arxiv")

router = APIRouter()

# arxiv id 형식 — 신형(2009.14191 / v 버전) + 구형(cs.CV/0512066). collect 입력 검증용.
_ARXIV_ID_RE = re.compile(r"^(?:[a-z\-]+/\d{7}|\d{4}\.\d{4,5})(?:v\d+)?$", re.IGNORECASE)

# ── 스키마 ──────────────────────────────────────────────────────────────

class KeywordCreate(BaseModel):
    keyword: str = Field(..., min_length=1, max_length=200)
    group_label: str | None = None      # 대표 키워드(그룹)에 넣기


class KeywordToggle(BaseModel):
    enabled: bool


class GroupRename(BaseModel):
    old_label: str = Field(..., min_length=1)
    new_label: str = Field(..., min_length=1, max_length=200)


class GroupClear(BaseModel):
    label: str = Field(..., min_length=1)


class ArxivSearchRequest(BaseModel):
    query: str = Field("", description="직접 쿼리 (비우면 keywords 로 build_query)")
    keywords: list[str] = Field(default_factory=list, description="키워드 리스트 (OR 결합)")
    max_results: int = Field(default=20, ge=1, le=100)   # 페이지 크기(서버 페이지네이션)
    offset: int = Field(default=0, ge=0)                  # 페이지 오프셋
    sort_by: str = Field(default="relevance")  # relevance | lastUpdatedDate | submittedDate
    date_from: str | None = None               # ISO (YYYY-MM-DD)
    date_to: str | None = None
    categories: list[str] = Field(default_factory=list)
    # 검색 범위 대분류(예: ['cs','eess','stat']). 빈 = 전체. 사용자가 UI 에서 선택.
    category_prefixes: list[str] = Field(default_factory=list)
    refine: str = Field(default="")  # 결과 내 검색(세분화) — 콤마/공백 AND
    wiki_filter: str = Field(default="all")  # all | has(위키 유) | none(위키 무)


class ArxivPaperOut(BaseModel):
    arxiv_id: str
    title: str
    summary: str = ""
    authors: list[str] = Field(default_factory=list)
    published: str | None = None
    categories: list[str] = Field(default_factory=list)
    abs_url: str = ""
    pdf_url: str = ""


class CollectRequest(BaseModel):
    arxiv_ids: list[str] = Field(..., min_length=1, max_length=50)


class CollectItemResult(BaseModel):
    arxiv_id: str
    ok: bool
    item_id: str | None = None
    created: bool | None = None
    title: str | None = None
    error: str | None = None


# ── 카테고리 (검색 범위 선택용) ────────────────────────────────────────

@router.get("/categories")
async def list_categories(
    _admin: dict = Depends(require_space_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """arxiv_papers 의 대분류(archive)별 논문 수 — 사용자가 검색 범위를 고르는 UI 용."""
    cats = await repository.arxiv_category_counts(session)
    return {"categories": cats}


# ── 키워드 CRUD ─────────────────────────────────────────────────────────

@router.get("/keywords")
async def list_keywords(
    _admin: dict = Depends(require_space_admin),
    space_id: UUID = Depends(get_current_space_id),
    session: AsyncSession = Depends(get_session),
) -> dict:
    rows = await repository.list_collection_keywords(session, space_id=space_id)
    # datetime → ISO 직렬화
    for r in rows:
        for k in ("created_at", "updated_at"):
            if r.get(k) is not None:
                r[k] = r[k].isoformat()
        r["id"] = str(r["id"])
        r["user_id"] = str(r["user_id"]) if r.get("user_id") else None
    return {"keywords": rows}


@router.post("/keywords")
async def add_keyword(
    payload: KeywordCreate,
    admin: dict = Depends(require_space_admin),
    space_id: UUID = Depends(get_current_space_id),
    session: AsyncSession = Depends(get_session),
) -> dict:
    kw = payload.keyword.strip()
    if not kw:
        raise HTTPException(status_code=400, detail="키워드가 비어 있습니다")
    group = (payload.group_label or "").strip() or None
    row = await repository.add_collection_keyword(
        session, space_id=space_id, user_id=admin["id"], keyword=kw, group_label=group,
    )
    await session.commit()
    if row is None:
        return {"created": False, "keyword": kw}
    return {
        "created": True, "id": str(row["id"]), "keyword": row["keyword"],
        "group_label": row.get("group_label"),
    }


@router.post("/keywords/group/rename")
async def rename_group(
    payload: GroupRename,
    _admin: dict = Depends(require_space_admin),
    space_id: UUID = Depends(get_current_space_id),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """대표 키워드(그룹) 이름 변경 — 그 그룹 모든 키워드 group_label 갱신."""
    n = await repository.rename_keyword_group(
        session, space_id=space_id,
        old_label=payload.old_label.strip(), new_label=payload.new_label.strip(),
    )
    await session.commit()
    return {"renamed": n}


@router.post("/keywords/group/clear")
async def clear_group(
    payload: GroupClear,
    _admin: dict = Depends(require_space_admin),
    space_id: UUID = Depends(get_current_space_id),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """대표 키워드(그룹) 삭제 — 그 그룹 키워드들을 미분류로(키워드는 보존)."""
    n = await repository.clear_keyword_group(
        session, space_id=space_id, label=payload.label.strip(),
    )
    await session.commit()
    return {"cleared": n}


@router.delete("/keywords/{keyword_id}")
async def delete_keyword(
    keyword_id: UUID,
    _admin: dict = Depends(require_space_admin),
    space_id: UUID = Depends(get_current_space_id),
    session: AsyncSession = Depends(get_session),
) -> dict:
    ok = await repository.delete_collection_keyword(
        session, space_id=space_id, keyword_id=keyword_id,
    )
    await session.commit()
    if not ok:
        raise HTTPException(status_code=404, detail="키워드를 찾을 수 없습니다")
    return {"deleted": True}


@router.patch("/keywords/{keyword_id}")
async def toggle_keyword(
    keyword_id: UUID,
    payload: KeywordToggle,
    _admin: dict = Depends(require_space_admin),
    space_id: UUID = Depends(get_current_space_id),
    session: AsyncSession = Depends(get_session),
) -> dict:
    ok = await repository.set_collection_keyword_enabled(
        session, space_id=space_id, keyword_id=keyword_id, enabled=payload.enabled,
    )
    await session.commit()
    if not ok:
        raise HTTPException(status_code=404, detail="키워드를 찾을 수 없습니다")
    return {"enabled": payload.enabled}


# ── arxiv 검색 미리보기 (DB 저장 X) ─────────────────────────────────────

def _parse_iso_date(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _row_to_paper_out(r: dict) -> ArxivPaperOut:
    """arxiv_papers row dict → ArxivPaperOut (abs/pdf URL 합성, published ISO)."""
    aid = r["arxiv_id"]
    pub = r.get("published")
    return ArxivPaperOut(
        arxiv_id=aid,
        title=r.get("title") or "",
        summary=r.get("abstract") or "",
        authors=list(r.get("authors") or []),
        published=pub.isoformat() if pub else None,
        categories=list(r.get("categories") or []),
        abs_url=f"https://arxiv.org/abs/{aid}",
        pdf_url=f"https://arxiv.org/pdf/{aid}",
    )


@router.post("/search")
async def search_arxiv_preview(
    payload: ArxivSearchRequest,
    _admin: dict = Depends(require_space_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """등록 키워드/쿼리로 **로컬 arxiv_papers FTS** 검색 (2026-06-05, rate limit 0).

    이전엔 arxiv API 직접 호출 → 429 rate limit. 이제 우리 DB(arxiv_papers, Kaggle+OAI
    적재)를 키워드별 FTS union 검색 → dedup → published DESC. 외부 호출 없음.
    """
    if payload.keywords:
        kws = [k for k in payload.keywords if k.strip()]   # 상한 없음 — 그룹 전체 키워드 OR
    elif payload.query.strip():
        kws = [payload.query.strip()]
    else:
        return {"papers": [], "count": 0, "total": 0}

    wmode = payload.wiki_filter if payload.wiki_filter in ("has", "none") else "all"
    collected_ids = await repository.all_collected_arxiv_ids(session) if wmode != "all" else None
    rows, total = await repository.search_arxiv_papers(
        session,
        keywords=kws,
        date_from=_parse_iso_date(payload.date_from),
        date_to=_parse_iso_date(payload.date_to),
        categories=payload.categories or None,
        category_prefixes=payload.category_prefixes or None,
        refine=payload.refine or None,
        wiki_mode=wmode,
        collected_ids=collected_ids,
        limit=payload.max_results,
        offset=payload.offset,
    )
    papers = await _papers_with_status(session, rows)
    return {"papers": papers, "count": len(papers), "total": total}


async def _papers_with_status(session: AsyncSession, rows: list[dict]) -> list[dict]:
    """검색/피드 공통 — arxiv_papers row 목록을 ArxivPaperOut + 수집상태(collected/item_id)
    로 변환. 이미 수집된 논문은 우측 위키 패널로 바로 열 수 있게 item_id 를 준다."""
    collected = await repository.find_collected_arxiv(
        session, [r["arxiv_id"] for r in rows],
    )
    # 수집된 item 들의 위키 상태(completed/pending/issues) — 3-state 버튼용
    wiki_status = await repository.wiki_status_by_items(
        session, [iid for iid in collected.values() if iid],
    )
    out = []
    for r in rows:
        po = _row_to_paper_out(r).model_dump()
        iid = collected.get(r["arxiv_id"])
        po["collected"] = iid is not None
        po["item_id"] = iid
        po["wiki_status"] = wiki_status.get(iid) if iid else None
        out.append(po)
    return out


# ── 피드 (등록 키워드별 최신 논문 + 수집상태) ──────────────────────────

@router.get("/feed")
async def arxiv_feed(
    limit: int = 20,
    offset: int = 0,
    cats: str = "",   # 대분류 콤마구분 (예: 'cs,eess,stat'). 빈 = 전체
    refine: str = "",  # 결과 내 검색(세분화)
    wiki: str = "all",  # all | has(위키 유) | none(위키 무)
    _admin: dict = Depends(require_space_admin),
    space_id: UUID = Depends(get_current_space_id),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """활성(enabled) 등록 키워드에 매칭되는 **최신 arxiv 논문 피드** + 수집상태.

    서버 페이지네이션(offset/limit) + total. 사용자 비전: '등록 키워드의 최신 논문을
    빠르게 위키로'. rate limit 0.
    """
    kws_rows = await repository.list_collection_keywords(session, space_id=space_id)
    enabled = [k["keyword"] for k in kws_rows if k.get("enabled")]
    if not enabled:
        return {"papers": [], "count": 0, "total": 0, "keywords": 0}

    cat_majors = [c.strip() for c in cats.split(",") if c.strip()] or None
    wmode = wiki if wiki in ("has", "none") else "all"
    collected_ids = await repository.all_collected_arxiv_ids(session) if wmode != "all" else None
    rows, total = await repository.search_arxiv_papers(
        session, keywords=enabled,   # 상한 없음 — 활성 키워드 전체 OR
        category_prefixes=cat_majors, refine=refine or None,
        wiki_mode=wmode, collected_ids=collected_ids, limit=limit, offset=offset,
    )
    papers = await _papers_with_status(session, rows)
    return {"papers": papers, "count": len(papers), "total": total, "keywords": len(enabled)}


# ── 수집 (선택 arxiv_id → pdf ingest → 위키 자동) ───────────────────────

@router.post("/collect")
async def collect_arxiv(
    payload: CollectRequest,
    background: BackgroundTasks,
    _admin: dict = Depends(require_space_admin),
) -> dict:
    """선택된 arxiv_id 들을 https://arxiv.org/pdf/{id} 로 순차 ingest_pdf.

    각 ingest 가 _wrap_result 로 classifier BackgroundTask 를 스케줄 → wiki daemon 이
    논문 위키 자동 합성. dedup 은 ingest_pdf 의 raw hash(UNIQUE) 가 처리.
    """
    results: list[CollectItemResult] = []
    for raw_id in payload.arxiv_ids:
        arxiv_id = (raw_id or "").strip()
        if not _ARXIV_ID_RE.match(arxiv_id):
            results.append(CollectItemResult(
                arxiv_id=arxiv_id, ok=False, error="잘못된 arxiv id 형식",
            ))
            continue
        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"
        try:
            resp = await ingest_pdf_endpoint(
                UrlIngestRequest(url=pdf_url, analyze_now=True, force=False),
                background,
            )
            results.append(CollectItemResult(
                arxiv_id=arxiv_id, ok=True, item_id=resp.item_id,
                created=resp.created, title=resp.title,
            ))
        except HTTPException as e:
            results.append(CollectItemResult(
                arxiv_id=arxiv_id, ok=False, error=f"{e.status_code}: {e.detail}",
            ))
        except Exception as e:  # noqa: BLE001 — 한 건 실패해도 나머지 진행
            logger.exception("arxiv collect 실패 (id=%s)", arxiv_id)
            results.append(CollectItemResult(arxiv_id=arxiv_id, ok=False, error=str(e)))

    ok_count = sum(1 for r in results if r.ok)
    return {
        "collected": ok_count,
        "total": len(results),
        "results": [r.model_dump() for r in results],
    }
