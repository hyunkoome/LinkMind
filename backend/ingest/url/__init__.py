"""
URL ingester — 주어진 URL 의 본문/메타데이터를 추출해서 LinkMind 에 넣는다.

수집 흐름
---------
1. fetch_html (httpx)
2. extract_doc: trafilatura → readability fallback 로 본문 + 메타.
   논문/article 페이지면 abstract 와 페이지 keywords 도 같이 뽑음.
3. raw_content = 본문 전체 (loss-less 저장)
4. embedding: 전체 본문을 chunk 로 잘라 Qdrant 색인
5. summary + tags: LLM 에 보내는 입력은 abstract 가 있으면 abstract 우선
   (논문은 전체 본문보다 abstract 가 더 정확한 요약 소스), 없으면 본문 앞부분.
   LLM 응답 마지막 줄의 `#tag1 #tag2 ...` 해시태그 + 페이지 메타 keywords 를
   합쳐 dedup 후 items.tags 에 저장. 이후 검색에서 `#tag` 로 필터링 가능.

사용 예 (REPL):
    >>> import asyncio
    >>> from backend.ingest.url import ingest_url
    >>> asyncio.run(ingest_url("https://arxiv.org/abs/2401.01234"))
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import httpx

from backend import runtime_settings
from backend.db.connection import get_engine
from backend.db.repository import (
    find_item_by_hash,
    find_or_create_topic,
    insert_chunks,
    insert_item,
    link_item_to_topic,
    update_item_analysis,
    update_item_metadata,
)
from backend.embedding.factory import get_embedding_provider
from backend.embedding.qdrant_store import (
    ensure_collection,
    set_payload_for_item_chunks,
    upsert_chunks,
)
from backend.llm.base import ChatMessage
from backend.llm.factory import get_llm_provider
from backend.utils.chunking import chunk_text
from backend.utils.external_ids import (
    ExternalId,
    extract_external_ids,
    primary_external_id,
    role_for_external_id,
)
from backend.utils.hashing import sha256_text
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

logger = logging.getLogger(__name__)

# 요약 LLM 입력 최대 길이 — abstract 가 없을 때 본문 앞부분만 잘라 보냄.
_SUMMARY_INPUT_LIMIT = 8000
# 태그 최소/최대 — UI 요구사항. 추출 결과가 부족해도 강제로 채우진 않음 (LLM 이
# 적게 뽑으면 적게).
_TAG_MAX = 10
_TAG_MIN = 5


# ──────────────────────────────────────────────────────────────
# Extraction
# ──────────────────────────────────────────────────────────────


@dataclass
class ExtractedDoc:
    """fetch + extract 결과. 모든 필드는 best-effort."""

    body: str                         # 본문 텍스트 (raw_content 저장용)
    title: str | None = None
    abstract: str | None = None       # 있으면 요약 입력으로 우선 사용
    paper_keywords: list[str] = field(default_factory=list)


async def fetch_html(url: str, timeout: float = 30.0) -> str:
    """주어진 URL 의 HTML 을 가져온다. 30xx 따라가고 비-2xx 는 raise.

    User-Agent 는 일반 브라우저 모방 — URL shortener (t.ly, lnkd.in, bit.ly 등)
    + 일부 사이트가 짧은/비표준 UA 를 봇으로 인식해 403 반환하는 케이스 회피.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 "
            "LinkMind/0.1 (+https://github.com/Real2Real-AI/LinkMind)"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=timeout,
        headers=headers,
    ) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.text


def extract_doc(html: str, url: str | None = None) -> ExtractedDoc:
    """본문 + 메타데이터(title, abstract, keywords) 추출.

    추출기 우선순위: trafilatura (본문/메타) → readability (본문) fallback.
    abstract 와 keywords 는 별도 HTML 파싱 (BeautifulSoup) — academic 사이트의
    citation_* meta 태그, arxiv 의 `<blockquote class="abstract">` 등.
    """
    body: str | None = None
    title: str | None = None

    # 1) trafilatura — 본문 + 기본 메타
    try:
        import trafilatura
        body = trafilatura.extract(
            html,
            url=url,
            include_comments=False,
            include_tables=True,
            favor_recall=True,
        )
        if body:
            md = trafilatura.metadata.extract_metadata(html)
            if md and md.title:
                title = md.title
    except Exception as e:  # noqa: BLE001
        logger.warning("trafilatura 추출 실패: %s", e)

    # 2) readability fallback
    if not body:
        try:
            from readability import Document
            doc = Document(html)
            body = doc.summary(html_partial=True)
            if not title:
                title = doc.title()
        except Exception as e:  # noqa: BLE001
            logger.warning("readability fallback 실패: %s", e)

    if not body:
        return ExtractedDoc(body="")

    # 3) abstract + keywords — BeautifulSoup 으로 별도 파싱
    abstract, keywords = _parse_paper_meta(html)

    return ExtractedDoc(
        body=body,
        title=title,
        abstract=abstract,
        paper_keywords=keywords,
    )


# 하위 호환 alias — 기존 코드/테스트가 (text, title) 튜플로 받는 경우 대비.
def extract_main_text(html: str, url: str | None = None) -> tuple[str | None, str | None]:
    doc = extract_doc(html, url=url)
    return (doc.body or None, doc.title)


def _parse_paper_meta(html: str) -> tuple[str | None, list[str]]:
    """논문/article HTML 에서 abstract 와 keywords 추출. 둘 다 best-effort."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
    except Exception as e:  # noqa: BLE001
        logger.warning("bs4/lxml 파싱 실패: %s", e)
        return None, []

    abstract: str | None = None

    # citation_abstract (Google Scholar / 학술 사이트 표준)
    tag = soup.find("meta", attrs={"name": "citation_abstract"})
    if tag and tag.get("content"):
        abstract = _clean_ws(tag["content"])

    # arxiv: <blockquote class="abstract">
    if not abstract:
        bq = soup.find("blockquote", class_=lambda c: c and "abstract" in c.split())
        if bq:
            # "Abstract:" prefix 제거
            txt = _clean_ws(bq.get_text(" ", strip=True))
            abstract = re.sub(r"^Abstract:\s*", "", txt)

    # og:description / description — 길면 abstract 후보
    if not abstract:
        for sel in ({"property": "og:description"}, {"name": "description"}):
            tag = soup.find("meta", attrs=sel)
            if tag and tag.get("content") and len(tag["content"]) > 200:
                abstract = _clean_ws(tag["content"])
                break

    # ── Keywords ──
    keywords: list[str] = []

    # citation_keywords (콤마/세미콜론 구분, 학술 표준)
    for name in ("citation_keywords", "keywords"):
        tag = soup.find("meta", attrs={"name": name})
        if tag and tag.get("content"):
            keywords.extend(_split_keyword_list(tag["content"]))

    # arxiv: subject classifications  e.g. "Methodology (stat.ME); Statistics (stat)"
    for td in soup.select("td.tablecell.subject"):
        keywords.extend(_split_keyword_list(td.get_text(" ", strip=True)))

    # JSON-LD keywords
    for ld in soup.find_all("script", type="application/ld+json"):
        try:
            import json
            data = json.loads(ld.string or "")
        except Exception:  # noqa: BLE001
            continue
        if isinstance(data, dict):
            kw = data.get("keywords")
            if isinstance(kw, str):
                keywords.extend(_split_keyword_list(kw))
            elif isinstance(kw, list):
                keywords.extend(str(k) for k in kw)

    return abstract, _normalize_tags(keywords)


_KEYWORD_SPLIT_RE = re.compile(r"[,;|]\s*")


def _split_keyword_list(s: str) -> list[str]:
    return [p.strip() for p in _KEYWORD_SPLIT_RE.split(s) if p.strip()]


def _clean_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


# ──────────────────────────────────────────────────────────────
# Tag normalization
# ──────────────────────────────────────────────────────────────


# 해시태그 형식: # 다음 영문/숫자/한글/하이픈/언더스코어
_HASHTAG_RE = re.compile(r"#([A-Za-z0-9가-힣_\-\.]+)")


def _extract_hashtags(text: str) -> list[str]:
    """텍스트 안의 모든 #tag 추출 (순서 보존, '#' 제외)."""
    return _HASHTAG_RE.findall(text or "")


def _normalize_tags(raw: list[str]) -> list[str]:
    """공백/구두점 제거, 길이 제한, case-insensitive dedup (첫 출현 form 유지)."""
    seen: dict[str, str] = {}
    for r in raw:
        t = r.strip().lstrip("#").strip(" \t\"'.,;:")
        if not t or len(t) > 50:
            continue
        key = t.casefold()
        if key in seen:
            continue
        seen[key] = t
    return list(seen.values())


# ──────────────────────────────────────────────────────────────
# Ingest pipeline
# ──────────────────────────────────────────────────────────────


async def ingest_url(
    url: str, *,
    analyze_now: bool = True,
    summarize: bool | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """URL 하나를 fetch + extract + DB 저장 + (옵션) 임베딩/요약.

    옵션 (3단계 분리, deferred processing 지원):
    - analyze_now=False: raw + topic link 만. chunks/summary 둘 다 skip. 보통 안 씀.
    - analyze_now=True, summarize=False: raw + chunks (embedding, 검색 가능) 까지 즉시.
                                          summary 만 백그라운드 worker 가 나중에 생성.
                                          → 텔레그램 backfill 빠르게 (1메시지 ~2-3초)
    - analyze_now=True, summarize=True (default): 모두 즉시 — 동기 처리.
    - summarize=None 이면 analyze_now 와 같은 값 (backward compat).

    force=True 면 동일 hash 의 기존 item 이 있어도 skip 하지 않고 분석 결과
    (summary, tags, source_metadata, title) 만 재계산해서 덮어쓴다. raw_content /
    chunks 는 동일 hash 라 의미상 같으므로 건드리지 않는다 (loss-less + 비용 최소).

    영구 실패 (HTTP 4xx, 본문 추출 실패) 시 fallback — URL 자체만 raw 로 보존하는
    "url-only" item 저장. 5xx / network error 는 raise (재시도 가능).

    Returns: {"item_id": ..., "created": bool, "refreshed": bool, "chunks_indexed": int,
              "summary_generated": bool, "tags": [...], "title": ...}
    """
    # backward compat — summarize 미지정 이면 analyze_now 따름.
    if summarize is None:
        summarize = analyze_now
    try:
        html = await fetch_html(url)
    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        # 4xx = 영구 실패 (403 Cloudflare/anti-bot, 404 사라짐, 410 gone, 401 unauth)
        # 5xx 는 raise — 일시 서버 오류, 재시도 가능
        if 400 <= status < 500:
            logger.info(
                "URL %s → %d (영구 실패, url-only 저장): %s",
                url, status, type(e).__name__,
            )
            return await _save_url_only(url, error=f"HTTP {status}", force=force)
        raise

    doc = extract_doc(html, url=url)
    if not doc.body or len(doc.body.strip()) < 50:
        # 200 OK 였지만 trafilatura + readability 모두 본문 추출 실패 (JS rendered,
        # 비표준 HTML 등). URL only 로 보존.
        logger.info("URL %s 200 OK 이지만 본문 추출 실패 (url-only 저장)", url)
        return await _save_url_only(url, error="본문 추출 실패", force=force)

    # 외부 식별자 (arxiv_id / doi / github_repo / yt video_id 등) — URL + 본문 모두에서.
    ext_ids = extract_external_ids(url=url, text=doc.body)

    content_hash = sha256_text(doc.body)

    engine = get_engine()
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        existing = await find_item_by_hash(
            session, source_type="url", content_hash=content_hash
        )
        if existing is not None:
            if not force:
                return {"item_id": str(existing), "created": False, "chunks_indexed": 0}
            refreshed = await refresh_existing_item_analysis(
                session,
                item_id=existing,
                doc=doc,
                source_metadata={
                    "has_abstract": bool(doc.abstract),
                    "paper_keywords": doc.paper_keywords,
                    "external_ids": [
                        {"kind": x.kind, "value": x.value} for x in ext_ids
                    ],
                },
            )
            await auto_link_topics(
                session, item_id=existing, source_type="url",
                title=doc.title, ids=ext_ids,
            )
            await session.commit()
            return {
                "item_id": str(existing),
                "created": False,
                "refreshed": True,
                "chunks_indexed": 0,
                "summary_generated": refreshed["summary"] is not None,
                "tags": refreshed["tags"],
                "title": doc.title,
            }

        item_id = await insert_item(
            session,
            source_type="url",
            raw_content=doc.body,
            raw_content_hash=content_hash,
            source_id=None,
            source_url=url,
            source_metadata={
                "has_abstract": bool(doc.abstract),
                "paper_keywords": doc.paper_keywords,
                "external_ids": [
                    {"kind": x.kind, "value": x.value} for x in ext_ids
                ],
            },
            title=doc.title,
            source_created_at=None,
        )
        # ingest 직후 topic auto-link — 새 item commit 전 같은 transaction 안에서.
        await auto_link_topics(
            session, item_id=item_id, source_type="url",
            title=doc.title, ids=ext_ids,
        )
        await session.commit()

        chunks_indexed = 0
        summary_text: str | None = None
        final_tags: list[str] = []
        if analyze_now:
            # embedding 은 빠름 (~1-2초) — 검색 기능 위해 즉시 처리
            chunks_indexed = await _embed_and_index(session, item_id=item_id, text=doc.body)
        if summarize:
            # summary 는 LLM 호출 (~30-60초) — summarize=False 면 skip,
            # 백그라운드 worker (lifespan) 가 summary IS NULL 인 row 처리.
            summary_text, final_tags = await _generate_and_save_summary(
                session,
                item_id=item_id,
                doc=doc,
            )

        return {
            "item_id": str(item_id),
            "created": True,
            "chunks_indexed": chunks_indexed,
            "summary_generated": summary_text is not None,
            "tags": final_tags,
            "title": doc.title,
        }


async def _save_url_only(
    url: str, *, error: str, force: bool = False,
) -> dict[str, Any]:
    """본문 추출 실패한 URL 을 'url-only' 로 저장 — graph 에 빈 노드로 보존.

    저장 내용:
      - source_type='url' (일반 url 과 같지만 raw_content 가 URL+에러 메시지만)
      - raw_content = URL + 에러 설명 (placeholder, hash 안정성 위해 고정 형식)
      - title = URL hostname
      - tags = ['url-only', 'fetch-failed']
      - summary 없음, chunks 없음 (raw 너무 짧고 의미 없음 — embedding 비용 회피)
      - source_metadata.fetch_error = 에러 정보 (사용자가 graph 에서 확인 가능)
      - external_ids 추출은 URL 에서만 (예: github URL 이면 owner/repo 따옴)

    dedup: 같은 URL 두 번 던지면 같은 raw_content hash → 두 번째는 created=False.
    사용자가 archive.org 또는 다른 mirror URL 로 던지면 새 item 으로 저장.
    """
    placeholder_text = (
        f"[url-only fallback — 본문 추출 실패]\n"
        f"URL: {url}\n"
        f"Error: {error}\n"
        f"이 자료의 본문을 가져오지 못했습니다. 수동으로 archive.org 또는 다른 mirror "
        f"URL 을 시도하거나, 이 노드를 graph 에서 삭제 후 재공유하세요."
    )
    content_hash = sha256_text(placeholder_text)

    # URL hostname 을 title 로 (예: medium.com — 어떤 사이트인지 즉시 인지)
    try:
        from urllib.parse import urlparse as _up
        title = _up(url).hostname or url
    except Exception:  # noqa: BLE001
        title = url

    # URL 의 external_ids — github URL 이면 owner/repo 추출 (본문 없어도 topic 자동 link)
    ext_ids = extract_external_ids(url=url, text=None)

    engine = get_engine()
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        existing = await find_item_by_hash(
            session, source_type="url", content_hash=content_hash,
        )
        if existing is not None and not force:
            return {
                "item_id": str(existing),
                "created": False,
                "url_only": True,
                "error": error,
            }

        if existing is not None:
            # force 면 dedup 무시하고 새 item 안 만듦 — 이미 url-only 라 재계산 의미 X
            return {
                "item_id": str(existing),
                "created": False,
                "url_only": True,
                "error": error,
            }

        item_id = await insert_item(
            session,
            source_type="url",
            raw_content=placeholder_text,
            raw_content_hash=content_hash,
            source_id=url,
            source_url=url,
            source_metadata={
                "fetch_error": error,
                "url_only": True,
                "external_ids": [{"kind": x.kind, "value": x.value} for x in ext_ids],
            },
            title=title,
            source_created_at=None,
        )
        await auto_link_topics(
            session, item_id=item_id, source_type="url",
            title=title, ids=ext_ids,
        )
        # url-only 면 의미상 'url-only' / 'fetch-failed' tag 도 — graph 에서 시각적 구분
        from sqlalchemy import text as _sql_text
        await session.execute(
            _sql_text("UPDATE items SET tags = :tags WHERE id = :id"),
            {"id": item_id, "tags": ["url-only", "fetch-failed"]},
        )
        await session.commit()

        return {
            "item_id": str(item_id),
            "created": True,
            "url_only": True,
            "error": error,
            "title": title,
        }


async def auto_link_topics(
    session: AsyncSession,
    *,
    item_id: UUID,
    source_type: str,
    title: str | None,
    ids: list[ExternalId],
) -> list[dict[str, Any]]:
    """item 의 external_ids 로 topic 자동 매핑.

    primary external_id 로 topic find_or_create → link. 추가로 발견된 다른 external_id
    (cross-modal 단서, 예: GitHub README 의 arxiv 링크) 도 topic 으로 매핑 — 단,
    그 단서들은 confidence 를 낮춰서 사용자 confirm 여지를 둠.

    Returns: 매핑된 topics 목록 [{topic_id, slug, role, ...}].
    """
    if not ids:
        return []
    primary = primary_external_id(ids)
    if primary is None:
        return []

    matched: list[dict[str, Any]] = []
    # 1차: primary external_id 로 main topic
    main_role = role_for_external_id(primary.kind, source_type)
    topic, _ = await find_or_create_topic(
        session,
        slug=primary.slug,
        title=title or primary.slug,
        primary_external_id={"kind": primary.kind, "value": primary.value},
    )
    await link_item_to_topic(
        session,
        item_id=item_id,
        topic_id=topic["id"],
        role=main_role,
        confidence=1.0,
        source="auto",
    )
    matched.append({**topic, "role": main_role, "confidence": 1.0})

    # 2차: 다른 external_id 들 — 보조 단서 (cross-modal). primary 와 같은 slug 면 skip.
    for x in ids:
        if x.slug == primary.slug:
            continue
        role = role_for_external_id(x.kind, source_type)
        side_topic, _ = await find_or_create_topic(
            session,
            slug=x.slug,
            title=title or x.slug,
            primary_external_id={"kind": x.kind, "value": x.value},
        )
        await link_item_to_topic(
            session,
            item_id=item_id,
            topic_id=side_topic["id"],
            role=role,
            confidence=0.7,            # cross-modal 단서는 자동이지만 신뢰도 낮춤
            source="auto",
        )
        matched.append({**side_topic, "role": role, "confidence": 0.7})

    return matched


async def refresh_existing_item_analysis(
    session: AsyncSession,
    *,
    item_id: UUID,
    doc: ExtractedDoc,
    source_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """force 재ingest 의 핵심 — raw/chunks 는 그대로, summary/tags/metadata 만 갱신.

    `_generate_and_save_summary` 는 update_item_analysis 의 COALESCE 흐름이라
    새 summary/tags 를 NULL 이 아닌 값으로 덮어쓴다. title/source_metadata 는
    update_item_metadata 로 별도 갱신 (예: 새 fetch 한 GitHub topics, license 등).
    """
    if source_metadata is not None or doc.title:
        await update_item_metadata(
            session,
            item_id=item_id,
            title=doc.title,
            source_metadata=source_metadata,
        )
        await session.commit()
    summary, tags = await _generate_and_save_summary(
        session, item_id=item_id, doc=doc,
    )
    return {"summary": summary, "tags": tags}


async def _generate_and_save_summary(
    session: AsyncSession, *, item_id: UUID, doc: ExtractedDoc,
) -> tuple[str | None, list[str]]:
    """LLM 으로 한국어 요약(+ 해시태그) 생성 → items.summary + tags 저장.

    Returns: (summary_text or None, final_tags). 실패해도 ingest 자체는 계속됨.
    """
    # abstract 가 있고 너무 짧지 않으면 우선 입력으로. 없으면 본문 앞부분.
    # 둘 다 _SUMMARY_INPUT_LIMIT 으로 cap — 플레이리스트처럼 abstract 자체가 매우 긴
    # 케이스(영상 목록 50+) 에서 모델 timeout/실패 방지.
    if doc.abstract and len(doc.abstract) >= 100:
        llm_input = doc.abstract[:_SUMMARY_INPUT_LIMIT]
        input_source = f"abstract[:{_SUMMARY_INPUT_LIMIT}]"
    else:
        llm_input = doc.body[:_SUMMARY_INPUT_LIMIT]
        input_source = f"body[:{_SUMMARY_INPUT_LIMIT}]"

    try:
        llm = get_llm_provider()
        prompt_version, prompt_content = runtime_settings.get_active_prompt("summary_system")
        # user message 에 한국어 강제 prefix — 영어 본문이 들어와도 출력은 한국어로
        # 끌고 가기 위한 reinforcement. 모델이 system instruction 보다 본문 언어에
        # 끌리는 케이스 (qwen2.5:14b 등) 방어.
        user_msg = (
            "아래 본문을 system prompt 의 형식과 규칙을 정확히 따라 **한국어로** 요약하라.\n"
            "본문이 영어/중국어/일본어 등 어떤 언어든 출력은 **무조건 한국어 bullet**.\n"
            "기술 용어/모델명/약어/고유명사만 원문(영어) 그대로 유지.\n\n"
            "---본문 시작---\n" + llm_input + "\n---본문 끝---"
        )
        resp = await llm.chat([
            ChatMessage(role="system", content=prompt_content),
            ChatMessage(role="user", content=user_msg),
        ])
    except Exception as e:  # noqa: BLE001
        # 빈 str(e) 도 종종 있음 (httpx timeout 등) — exception type 도 함께 로그.
        logger.warning(
            "요약 생성 실패 (ingest 는 계속): %s: %s",
            type(e).__name__, e or "(no message)",
        )
        # 그래도 paper_keywords 만이라도 tags 로 저장.
        if doc.paper_keywords:
            tags = _normalize_tags(doc.paper_keywords)[:_TAG_MAX]
            await update_item_analysis(
                session, item_id=item_id, summary=None, summary_model=None,
                summary_prompt_version=None, categories=None, tags=tags,
            )
            await session.commit()
            return None, tags
        return None, []

    # LLM hashtags + paper meta keywords 머지 → dedup → 길이 제한.
    llm_tags = _extract_hashtags(resp.text)
    merged = _normalize_tags([*doc.paper_keywords, *llm_tags])
    final_tags = merged[:_TAG_MAX]
    if len(final_tags) < _TAG_MIN:
        logger.info(
            "tags 수가 최소(%d) 미달: %d개 (item=%s). prompt 가 hashtag 줄을 안 뽑았거나 "
            "키워드 메타가 적은 페이지. summary 본문은 정상.",
            _TAG_MIN, len(final_tags), item_id,
        )

    await update_item_analysis(
        session,
        item_id=item_id,
        summary=resp.text,
        summary_model=f"{resp.provider}/{resp.model}",
        summary_prompt_version=prompt_version,
        categories=None,
        tags=final_tags,
    )
    await session.commit()

    # Qdrant chunk payload 의 tags 도 갱신 — 이제 #tag 검색이 Qdrant 필터 단계에서 동작.
    if final_tags:
        try:
            await set_payload_for_item_chunks(
                item_id=str(item_id), payload={"tags": final_tags},
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Qdrant chunk payload tags 갱신 실패 (검색은 영향 받을 수 있음): %s", e)

    logger.info(
        "요약 생성: item=%s, model=%s/%s, input=%s, len=%d, tags=%s",
        item_id, resp.provider, resp.model, input_source, len(resp.text), final_tags,
    )
    return resp.text, final_tags


async def _embed_and_index(
    session: AsyncSession, *, item_id: UUID, text: str,
) -> int:
    embedder = get_embedding_provider()
    await ensure_collection(dim=embedder.dim)
    chunks = chunk_text(text)
    if not chunks:
        return 0
    emb = await embedder.embed(chunks)
    chunk_ids = await insert_chunks(
        session,
        item_id=item_id,
        chunks=chunks,
        embedding_model=embedder.model,
        embedding_dim=embedder.dim,
    )
    await session.commit()
    payloads = [
        {
            "item_id": str(item_id),
            "chunk_index": idx,
            "source_type": "url",
            "snippet": ctext[:300],
        }
        for idx, ctext in enumerate(chunks)
    ]
    await upsert_chunks(
        chunk_ids=[str(cid) for cid in chunk_ids],
        vectors=emb.vectors,
        payloads=payloads,
    )
    return len(chunks)


# CLI 진입점은 backend/ingest/url/__main__.py 에 분리.
