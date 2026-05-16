"""
scripts/seed_arxiv_metadata.py
----------------------------------------------------------------------------
arxiv API (export.arxiv.org/api/query) 로 'arxiv:<id>' topic 들의 title /
authors / published_at / summary 자동 보강.

배경: ingest 시 topic 이 자동 생성되면 title 이 첫 발견 item 의 title 로
들어가는데, 그게 종종 부정확하거나 자식 item 의 title 과 다름. arxiv API 가
권위 있는 메타라 한 번 호출해 보강한다.

흐름:
1. topics 중 slug LIKE 'arxiv:%' + (title 이 slug 와 같거나 description NULL)
   인 row 만 후보 (이미 사람이 손댄 건 안 건드림).
2. arxiv API 호출 — 한 번에 최대 100개 id (`id_list=A,B,C,...`).
3. 응답 파싱 (XML Atom feed). 각 id 별로 title / summary / authors / published.
4. topics.title / topics.description (description 비었을 때만) UPDATE +
   source_metadata 에 arxiv_meta 키 머지.

rate limit: arxiv API 는 3 req/sec. 한 번에 100개 묶어 보내면 충분.

사용:
    python scripts/seed_arxiv_metadata.py              # 보강 필요한 topic 모두
    python scripts/seed_arxiv_metadata.py <slug>       # 특정 1개 (예: arxiv:2106.09685)
    python scripts/seed_arxiv_metadata.py --force      # 이미 보강된 것도 다시
"""

from __future__ import annotations

import asyncio
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


import httpx  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker  # noqa: E402

from backend.db.connection import get_engine  # noqa: E402


_ARXIV_API = "https://export.arxiv.org/api/query"
_ATOM_NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
_BATCH = 100  # arxiv API max id_list


async def _candidates(
    session: AsyncSession, *, slug: str | None, force: bool,
) -> list[tuple[str, str, str | None]]:
    """(slug, title, description) 반환. arxiv:* 토픽 중 보강 필요한 것."""
    if slug:
        rows = await session.execute(
            text("""
                SELECT slug, title, description FROM topics
                WHERE slug = :s AND slug LIKE 'arxiv:%'
            """),
            {"s": slug},
        )
    elif force:
        rows = await session.execute(text(
            "SELECT slug, title, description FROM topics WHERE slug LIKE 'arxiv:%'"
        ))
    else:
        # tags 안에 'arxiv-seeded' 가 없으면 아직 보강 안 된 것 — topics 테이블은
        # source_metadata 컬럼이 없으므로 tags 마커 + description 마커 둘 다 사용.
        rows = await session.execute(text(
            "SELECT slug, title, description FROM topics "
            "WHERE slug LIKE 'arxiv:%' "
            "  AND NOT ('arxiv-seeded' = ANY(tags))"
        ))
    return [(r.slug, r.title, r.description) for r in rows.all()]


async def _fetch_arxiv_batch(ids: list[str]) -> dict[str, dict[str, Any]]:
    """arxiv API 호출 — id_list 로 한 번에 N개. id → {title, summary, authors, published}."""
    params = {"id_list": ",".join(ids), "max_results": str(len(ids))}
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(_ARXIV_API, params=params)
        r.raise_for_status()
    root = ET.fromstring(r.text)
    out: dict[str, dict[str, Any]] = {}
    for entry in root.findall("atom:entry", _ATOM_NS):
        # id 는 'http://arxiv.org/abs/2106.09685v2' 같은 형태 — 정규화
        entry_id = (entry.findtext("atom:id", default="", namespaces=_ATOM_NS) or "")
        # 'abs/' 다음, 'v\d+' 제외
        bare = entry_id.rsplit("/abs/", 1)[-1].split("v")[0]
        title = (entry.findtext("atom:title", default="", namespaces=_ATOM_NS) or "").strip()
        summary = (entry.findtext("atom:summary", default="", namespaces=_ATOM_NS) or "").strip()
        published = (entry.findtext("atom:published", default="", namespaces=_ATOM_NS) or "").strip()
        authors = [
            (a.findtext("atom:name", default="", namespaces=_ATOM_NS) or "").strip()
            for a in entry.findall("atom:author", _ATOM_NS)
        ]
        primary_category = entry.find("arxiv:primary_category", _ATOM_NS)
        cat = primary_category.get("term") if primary_category is not None else None
        out[bare] = {
            "title": " ".join(title.split()),  # \n 제거
            "summary": " ".join(summary.split()),
            "authors": authors,
            "published": published,
            "primary_category": cat,
        }
    return out


def _format_description(meta: dict[str, Any], old_desc: str | None) -> str:
    """arxiv summary 를 description 형식으로. 기존 description 이 있으면 그대로 보존."""
    if old_desc:
        return old_desc
    lines = [meta["title"]]
    if meta.get("authors"):
        lines.append("")
        lines.append("저자: " + ", ".join(meta["authors"][:5]) + (" 등" if len(meta["authors"]) > 5 else ""))
    if meta.get("published"):
        lines.append(f"발행: {meta['published'][:10]}")
    if meta.get("primary_category"):
        lines.append(f"분야: {meta['primary_category']}")
    if meta.get("summary"):
        lines.append("")
        lines.append("Abstract: " + meta["summary"][:1500] + ("…" if len(meta["summary"]) > 1500 else ""))
    lines.append("")
    lines.append("<!-- arxiv API seeded -->")
    return "\n".join(lines)


async def main() -> int:
    args = sys.argv[1:]
    force = "--force" in args
    args = [a for a in args if a != "--force"]
    target_slug: str | None = args[0] if args else None

    engine = get_engine()
    sf = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with sf() as session:
        cands = await _candidates(session, slug=target_slug, force=force)
        if not cands:
            print("대상 없음 — arxiv:* topic 의 메타가 모두 보강된 상태.")
            return 0

        print(f"대상 {len(cands)} 건 — arxiv API 보강 시작")
        ok, fail = 0, 0
        # batch 단위 호출
        for i in range(0, len(cands), _BATCH):
            chunk = cands[i:i + _BATCH]
            ids = [slug.split(":", 1)[1] for slug, _, _ in chunk]
            try:
                meta_map = await _fetch_arxiv_batch(ids)
            except Exception as e:  # noqa: BLE001
                print(f"  arxiv API 실패 (batch {i}-{i+len(chunk)}): {e}")
                fail += len(chunk)
                continue

            for slug, _old_title, old_desc in chunk:
                arxiv_id = slug.split(":", 1)[1]
                meta = meta_map.get(arxiv_id)
                if not meta:
                    print(f"  - {slug} ... arxiv 응답에 없음")
                    fail += 1
                    continue
                new_title = meta["title"] or slug
                new_desc = _format_description(meta, old_desc)
                await session.execute(
                    text("""
                        UPDATE topics SET
                            title = :title,
                            description = :desc,
                            tags = CASE
                                WHEN 'arxiv-seeded' = ANY(tags) THEN tags
                                ELSE array_append(tags, 'arxiv-seeded')
                            END
                        WHERE slug = :slug
                    """),
                    {
                        "title": new_title[:500],
                        "desc": new_desc,
                        "slug": slug,
                    },
                )
                print(f"  - {slug} ... OK ({new_title[:60]})")
                ok += 1
            await session.commit()

        print(f"\n완료: 성공 {ok} / 실패 {fail}")
        return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
