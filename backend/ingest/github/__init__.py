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
import os
import re
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from backend.db.connection import get_engine
from backend.db.repository import find_item_by_hash, insert_item
from backend.ingest.url import (
    ExtractedDoc,
    _embed_and_index,
    _generate_and_save_summary,
    refresh_existing_item_analysis,
)
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
    h = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "LinkMind/0.1",
    }
    token = os.getenv("GITHUB_TOKEN", "").strip()
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


async def ingest_github(
    url: str, *, analyze_now: bool = True, force: bool = False,
) -> dict[str, Any]:
    owner, repo = parse_github_url(url)
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

    paper_links = _detect_paper_links(readme_text + " " + description)

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
    header.append(readme_text or "(no README)")
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
        },
        analyze_now=analyze_now,
        force=force,
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
            if not force:
                return {"item_id": str(existing), "created": False, "chunks_indexed": 0}
            refreshed = await refresh_existing_item_analysis(
                session, item_id=existing, doc=doc, source_metadata=source_metadata,
            )
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
