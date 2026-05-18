"""
GitHub repository ingester.

흐름
----
1. URL → (owner, repo) 파싱
2. GitHub REST API (`/repos/{owner}/{repo}`, `/readme`, `/languages`) 호출.
   `GITHUB_TOKEN` 환경변수 있으면 인증 (rate limit 60 → 5000/hour).
3. raw_content = README 본문 + 메타 헤더. (loss-less, 줄여서 저장하지 않음)
4. 라이선스 SPDX id (MIT, Apache-2.0, GPL-3.0 등) 를 해시태그 라벨로 paper_keywords 에 강제 주입.
   언어, GitHub topics 도 paper_keywords 로 합류.
5. items 로 저장 후 url ingest 와 동일한 helper 로 임베딩 + 요약 + 해시태그.
   요약 LLM 입력은 README 본문 (앞 8000자 — _SUMMARY_INPUT_LIMIT 와 동일).
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from backend.config import get_settings
from backend.db.connection import get_engine
from backend.db.repository import find_item_by_hash, insert_item
from backend.ingest.url import (
    ExtractedDoc,
    _embed_and_index,
    _generate_and_save_summary,
    auto_link_topics,
    refresh_existing_item_analysis,
)
from backend.utils.external_ids import ExternalId, extract_external_ids
from backend.utils.hashing import sha256_text

logger = logging.getLogger(__name__)


_GH_API = "https://api.github.com"


def parse_github_url(url: str) -> tuple[str, str]:
    """`https://github.com/<owner>/<repo>` → (owner, repo). .git suffix 도 처리."""
    u = urlparse(url)
    if (u.hostname or "").lower() not in {"github.com", "www.github.com"}:
        raise ValueError(f"GitHub URL 이 아닙니다: {url}")
    parts = [p for p in u.path.split("/") if p]
    if len(parts) < 2:
        raise ValueError(f"GitHub URL 에서 owner/repo 추출 실패: {url}")
    owner, repo = parts[0], parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    return owner, repo


def _headers() -> dict[str, str]:
    """GitHub API headers — token 있으면 rate limit 5000/hour, 없으면 60/hour."""
    h = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        # 일반 브라우저 UA 모방 — 일부 GitHub raw/contents endpoint 가 짧은 UA 거부
        "User-Agent": "LinkMind/0.1 (+https://github.com/Real2Real-AI/LinkMind)",
    }
    token = (get_settings().github_token or "").strip()
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


async def _gh_get(client: httpx.AsyncClient, path: str) -> dict[str, Any] | None:
    r = await client.get(f"{_GH_API}{path}", headers=_headers(), timeout=30.0)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


_PAPER_LINK_RE = re.compile(
    r"https?://(?:arxiv\.org/abs/[0-9.]+|doi\.org/[^\s)]+|paperswithcode\.com/[^\s)]+)",
    re.IGNORECASE,
)


def _detect_paper_links(text: str) -> list[str]:
    return list(dict.fromkeys(_PAPER_LINK_RE.findall(text or "")))


def _clean_readme_html(md: str) -> str:
    """GitHub README 안의 raw HTML tag 제거. <a href> 와 <img alt> 의 정보는 보존.

    GitHub README 는 markdown 인데 종종 raw HTML (`<h2 style="...">`, `<a href>`,
    `<img>`, `<center>`, `<details>`) 가 inline 으로 섞임. 그게 그대로 chunks/snippet
    에 들어가면 검색 결과가 너저분하고 LLM 요약 입력도 노이즈. HTML 만 제거하고
    안의 텍스트는 보존, `<a href>` 는 markdown 링크 형식으로 변환 — paper_links
    검출 정규식이 그대로 동작하도록.
    """
    if not md:
        return ""
    try:
        from bs4 import BeautifulSoup
    except Exception as e:  # noqa: BLE001
        logger.warning("bs4 import 실패 — HTML strip skip: %s", e)
        return md

    soup = BeautifulSoup(md, "lxml")

    # <a href> → markdown 링크 (정보 보존). text 가 비어있으면 href 자체를 표시.
    for a in soup.find_all("a"):
        href = (a.get("href") or "").strip()
        text = (a.get_text() or "").strip() or href
        if href:
            a.replace_with(f"[{text}]({href})")
        else:
            a.replace_with(text)

    # <img alt="..."> → "[image: alt]". alt 없으면 제거.
    for img in soup.find_all("img"):
        alt = (img.get("alt") or "").strip()
        if alt:
            img.replace_with(f"[image: {alt}]")
        else:
            img.decompose()

    # <code>/<pre> 의 내용은 inline code 로 (markdown 의 backtick 과 충돌 안 함)
    for code in soup.find_all(["code", "pre"]):
        code.replace_with(code.get_text() or "")

    text = soup.get_text("\n")
    # 연속 빈 줄 압축 (BeautifulSoup 출력이 종종 3+ 줄 생성)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


async def ingest_github(
    url: str, *,
    analyze_now: bool = True,
    force: bool = False,
    caption: str | None = None,
) -> dict[str, Any]:
    # owner-only URL (예: https://github.com/graphdeco-inria) 는 repo 가 없어
    # repo API 호출 불가 — url ingest 로 폴백 (organization page 의 HTML 만이라도 보존).
    try:
        owner, repo = parse_github_url(url)
    except ValueError:
        logger.info("GitHub URL 에 repo 가 없어 url ingest 로 폴백: %s", url)
        from backend.ingest.url import ingest_url
        return await ingest_url(
            url, analyze_now=analyze_now, force=force, caption=caption,
        )
    canonical = f"https://github.com/{owner}/{repo}"

    async with httpx.AsyncClient(follow_redirects=True) as client:
        meta = await _gh_get(client, f"/repos/{owner}/{repo}")
        if not meta:
            raise ValueError(f"GitHub repo 가 존재하지 않거나 비공개입니다: {owner}/{repo}")
        langs = await _gh_get(client, f"/repos/{owner}/{repo}/languages") or {}
        readme = await _gh_get(client, f"/repos/{owner}/{repo}/readme")

    description = (meta.get("description") or "").strip()
    primary_lang = meta.get("language") or ""
    topics = meta.get("topics") or []
    stars = meta.get("stargazers_count") or 0
    forks = meta.get("forks_count") or 0
    license_info = meta.get("license") or {}
    license_spdx = (license_info.get("spdx_id") or "").strip()
    license_name = (license_info.get("name") or "").strip()
    homepage = (meta.get("homepage") or "").strip()
    default_branch = meta.get("default_branch") or "main"

    readme_text = ""
    readme_path = None
    if readme and readme.get("content"):
        import base64
        try:
            readme_text = base64.b64decode(readme["content"]).decode("utf-8", errors="replace")
            readme_path = readme.get("path")
        except Exception as e:  # noqa: BLE001
            logger.warning("README decode 실패: %s", e)

    # paper_links 는 raw README 기준으로 잡되 (모든 URL 형식 포함), raw_body 와
    # LLM 요약 입력에는 HTML strip 한 cleaned text 를 사용 — 검색 snippet 도 깨끗.
    paper_links = _detect_paper_links(readme_text + " " + description)
    readme_clean = _clean_readme_html(readme_text)

    # raw_body 는 "콘텐츠" 만 포함 — stars/forks 같이 시간에 따라 변하는 카운터는
    # source_metadata 에만. 그래야 raw_content_hash 가 안정 → force 재ingest 가
    # 같은 item 매칭에 성공 (raw-first + idempotent 원칙).
    # languages dict 도 key 순서 비결정적이라 정렬해서 직렬화.
    header = [
        f"GitHub: {owner}/{repo}",
        f"URL: {canonical}",
        f"Description: {description}" if description else "Description: (none)",
        f"Primary language: {primary_lang or '(unknown)'}",
        f"Languages: {', '.join(sorted(langs.keys())) if langs else '(none)'}",
        f"License: {license_spdx or license_name or '(no license)'}",
        f"Homepage: {homepage}" if homepage else "",
        f"Topics: {', '.join(sorted(topics)) if topics else '(none)'}",
    ]
    if paper_links:
        header.append("Paper links: " + ", ".join(paper_links))
    header.append(f"Default branch: {default_branch}")
    header.append("")
    header.append(f"## README ({readme_path or 'not found'})")
    # raw_body 에는 HTML strip 한 cleaned README 사용 — chunks/snippet 깨끗 +
    # LLM 요약 입력 노이즈 제거. 원본 markdown 은 source_metadata.readme_raw 에 보관.
    header.append(readme_clean or "(no README)")
    raw_body = "\n".join(p for p in header if p is not None)

    # 라이선스를 해시태그 형태로 강제 주입 (요청사항). SPDX id 가 있으면 그걸,
    # 없으면 license_name 을 슬러그화, 둘 다 없으면 `no-license`.
    license_tag = license_spdx or _slugify(license_name) or "no-license"

    # 순서가 final_tags 자리 선점을 결정 (_TAG_MAX 만큼만 살아남음).
    # license 와 primary_lang 은 사용자가 명시한 우선 키워드 — 무조건 들어가도록 맨 앞.
    paper_keywords: list[str] = [
        license_tag,
        *([primary_lang] if primary_lang else []),
        *(["has-paper-link"] if paper_links else []),
        *topics,
    ]

    abstract = description if (description and len(description) >= 60) else None

    # github repo 자체 + README 안의 arxiv/doi/다른 github 링크 → external_ids.
    ext_ids: list[ExternalId] = [ExternalId(kind="github", value=f"{owner}/{repo}")]
    for x in extract_external_ids(text=readme_text + " " + description):
        # 자기 자신 (github:{owner}/{repo}) 은 위에서 이미 넣음 — dedup
        if x.kind == "github" and x.value == f"{owner}/{repo}":
            continue
        ext_ids.append(x)

    doc = ExtractedDoc(
        body=raw_body, title=f"{owner}/{repo}",
        abstract=abstract, paper_keywords=paper_keywords,
    )

    return await _save_with_summary(
        doc=doc,
        source_type="github",
        source_id=f"{owner}/{repo}",
        source_url=canonical,
        source_metadata={
            "owner": owner,
            "repo": repo,
            "languages": langs,
            "primary_language": primary_lang,
            "topics": topics,
            "stars": stars,
            "forks": forks,
            "license_spdx": license_spdx,
            "license_name": license_name,
            "homepage": homepage,
            "default_branch": default_branch,
            "paper_links": paper_links,
            "external_ids": [{"kind": x.kind, "value": x.value} for x in ext_ids],
            "readme_raw_len": len(readme_text),  # 원본 길이 (디버깅)
            "readme_clean_len": len(readme_clean),
        },
        analyze_now=analyze_now,
        force=force,
        external_ids=ext_ids,
        caption=caption,
    )


_SLUG_RE = re.compile(r"[^A-Za-z0-9가-힣]+")


def _slugify(s: str) -> str:
    s = _SLUG_RE.sub("-", s).strip("-")
    return s


async def _save_with_summary(
    *,
    doc: ExtractedDoc,
    source_type: str,
    source_id: str,
    source_url: str,
    source_metadata: dict[str, Any],
    analyze_now: bool,
    force: bool = False,
    external_ids: list[ExternalId] | None = None,
    caption: str | None = None,
) -> dict[str, Any]:
    if not doc.body or len(doc.body.strip()) < 50:
        raise ValueError("본문이 너무 짧아 저장할 수 없습니다")

    content_hash = sha256_text(doc.body)
    engine = get_engine()
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        existing = await find_item_by_hash(
            session, source_type=source_type, content_hash=content_hash,
        )
        if existing is not None:
            # 새 caption 이면 user_notes append (dedup 에서도 사용자 메모 보존)
            if caption and caption.strip():
                from backend.db.repository import append_item_user_notes
                await append_item_user_notes(
                    session, item_id=existing, new_note=caption.strip(),
                )
                await session.commit()
            if not force:
                return {"item_id": str(existing), "created": False, "chunks_indexed": 0}
            refreshed = await refresh_existing_item_analysis(
                session, item_id=existing, doc=doc, source_metadata=source_metadata,
            )
            if external_ids:
                await auto_link_topics(
                    session, item_id=existing, source_type=source_type,
                    title=doc.title, ids=external_ids,
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
            source_type=source_type,
            raw_content=doc.body,
            raw_content_hash=content_hash,
            source_id=source_id,
            source_url=source_url,
            source_metadata=source_metadata,
            title=doc.title,
            source_created_at=None,
        )
        if external_ids:
            await auto_link_topics(
                session, item_id=item_id, source_type=source_type,
                title=doc.title, ids=external_ids,
            )
        # caption (텔레그램 등에서 같이 온 사용자 메모) → user_notes
        if caption and caption.strip():
            from backend.db.repository import append_item_user_notes
            await append_item_user_notes(
                session, item_id=item_id, new_note=caption.strip(),
            )
        await session.commit()

        chunks_indexed = 0
        summary_text: str | None = None
        tags: list[str] = []
        if analyze_now:
            chunks_indexed = await _embed_and_index(session, item_id=item_id, text=doc.body)
            summary_text, tags = await _generate_and_save_summary(
                session, item_id=item_id, doc=doc,
            )

        return {
            "item_id": str(item_id),
            "created": True,
            "chunks_indexed": chunks_indexed,
            "summary_generated": summary_text is not None,
            "tags": tags,
            "title": doc.title,
        }
