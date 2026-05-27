"""
wiki_backfill_titles.py — wiki_pages 의 title 이 URL/slug 인 경우 진짜 제목 채우기.

배경 (사용자 명시 2026-05-26):
  - "doi/arxiv 링크 기반 wiki 페이지 생성 시 논문 제목이 title 로 들어가야 하는데
    링크 URL 이 title 로 들어감 — 수정 필요"
  - 진단: arxiv__* 1,708개 + url__* / github__* / yt__* 등 2,283개 wiki_pages 의 title
    이 URL 또는 'arxiv:N.NNNN' 같은 slug-form

전략:
  1. arxiv__* — arxiv API (export.arxiv.org/api/query) 로 진짜 논문 제목 + abstract
     호출 (배치 100개씩, rate limit 3 req/s 안전)
  2. 그 외 (url__*, yt__* 등) — 같은 wiki_page 에 link 된 items 중 가장 정보 많은 title
     선택 (URL 가 아니고 + 길이 충분 + slug 와 다른 것)

idempotent — title 이 이미 좋으면 skip. --force 로 무조건 갱신.

사용:
  python -m backend.jobs.wiki_backfill_titles                  # arxiv + items.title 둘 다
  python -m backend.jobs.wiki_backfill_titles --only-arxiv     # arxiv API 만
  python -m backend.jobs.wiki_backfill_titles --only-items     # items.title 만 (빠름)
  python -m backend.jobs.wiki_backfill_titles --limit 100      # 일부만
  python -m backend.jobs.wiki_backfill_titles --dry-run        # 처리 안 함
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import xml.etree.ElementTree as ET
from typing import Any

import httpx
from sqlalchemy import text
from tqdm import tqdm

from backend.db.connection import close_engine, get_session_factory

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s : %(message)s",
)
logger = logging.getLogger("linkmind.jobs.wiki_backfill_titles")


_ARXIV_API = "https://export.arxiv.org/api/query"
_ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}
_ARXIV_BATCH = 100
_RATE_SLEEP_S = 0.4   # 3 req/s 안전 margin

# bad title 패턴 — URL / slug / 빈 것
_BAD_TITLE_RE = re.compile(r"^(https?://|arxiv:|doi:|ytpl?:|github:)", re.IGNORECASE)


def _is_bad_title(title: str | None, slug: str) -> bool:
    if not title:
        return True
    if _BAD_TITLE_RE.match(title):
        return True
    if title == slug:
        return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# arxiv API
# ─────────────────────────────────────────────────────────────────────────────

async def _fetch_arxiv_batch(ids: list[str]) -> dict[str, dict[str, Any]]:
    """arxiv API 호출 — 한 번에 N개. id → {title, summary, authors}."""
    params = {"id_list": ",".join(ids), "max_results": str(len(ids))}
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(_ARXIV_API, params=params)
        r.raise_for_status()
        text_body = r.text

    out: dict[str, dict[str, Any]] = {}
    root = ET.fromstring(text_body)
    for entry in root.findall("atom:entry", _ATOM_NS):
        id_el = entry.find("atom:id", _ATOM_NS)
        if id_el is None or not id_el.text:
            continue
        # id format: http://arxiv.org/abs/2106.09685v2 — 끝 vN 제거
        m = re.search(r"abs/([\d.]+)", id_el.text)
        if not m:
            continue
        arxiv_id = m.group(1)

        title_el = entry.find("atom:title", _ATOM_NS)
        sum_el = entry.find("atom:summary", _ATOM_NS)
        authors = [
            (a.find("atom:name", _ATOM_NS).text or "")
            for a in entry.findall("atom:author", _ATOM_NS)
            if a.find("atom:name", _ATOM_NS) is not None
        ]
        out[arxiv_id] = {
            "title": (title_el.text or "").strip().replace("\n", " ") if title_el is not None else None,
            "summary": (sum_el.text or "").strip().replace("\n", " ") if sum_el is not None else None,
            "authors": authors,
        }
    return out


# slug 'arxiv__2106.09685' → '2106.09685'
def _arxiv_id_from_slug(slug: str) -> str | None:
    if not slug.startswith("arxiv__"):
        return None
    return slug[len("arxiv__"):]


async def _fix_arxiv_titles(limit: int, dry_run: bool) -> tuple[int, int]:
    """arxiv__* wiki_pages 중 bad title 인 것 arxiv API 로 fix.

    반환 (총 처리, 갱신).
    """
    session_factory = get_session_factory()
    async with session_factory() as session:
        async with session.begin():
            rows = (await session.execute(text("""
                SELECT id, slug, title FROM wiki_pages
                WHERE slug LIKE 'arxiv__%'
                  AND (title LIKE 'arxiv:%' OR title LIKE 'http%' OR title = slug)
                ORDER BY slug
                LIMIT :lim
            """), {"lim": limit})).mappings().all()

    total = len(rows)
    if total == 0:
        print("✅ arxiv title 모두 정상 — 갱신 대상 없음")
        return 0, 0

    print(f"📚 arxiv title fix — {total} pages (batch={_ARXIV_BATCH}, rate {_RATE_SLEEP_S}s)")
    if dry_run:
        for r in rows[:10]:
            print(f"  • {r['slug']}: {r['title']}")
        print("--- DRY RUN — 처리 안 함 ---")
        return total, 0

    # batch 100 씩
    updated = 0
    bar = tqdm(total=total, desc="📚 arxiv API + UPDATE", unit="page", mininterval=0.5)
    for i in range(0, total, _ARXIV_BATCH):
        batch_rows = rows[i:i + _ARXIV_BATCH]
        ids = [_arxiv_id_from_slug(r["slug"]) for r in batch_rows]
        ids = [x for x in ids if x]
        try:
            meta = await _fetch_arxiv_batch(ids)
        except Exception as e:  # noqa: BLE001
            bar.write(f"  ⚠️  arxiv API 실패 (batch {i}): {e}")
            bar.update(len(batch_rows))
            await asyncio.sleep(_RATE_SLEEP_S)
            continue

        # update batch
        async with session_factory() as session:
            async with session.begin():
                for r in batch_rows:
                    aid = _arxiv_id_from_slug(r["slug"])
                    if not aid or aid not in meta:
                        bar.update(1)
                        continue
                    m = meta[aid]
                    new_title = m.get("title")
                    new_desc = m.get("summary")
                    if not new_title:
                        bar.update(1)
                        continue
                    # description 도 비어있으면 channel 채움 (arxiv abstract 의 첫 300자)
                    set_desc = (
                        ", description = :desc" if new_desc else ""
                    )
                    await session.execute(
                        text(f"""
                            UPDATE wiki_pages
                            SET title = :title{set_desc}
                            WHERE id = :pid
                        """),
                        {
                            "title": new_title,
                            "pid": str(r["id"]),
                            **({"desc": new_desc[:500]} if new_desc else {}),
                        },
                    )
                    updated += 1
                    bar.update(1)
        await asyncio.sleep(_RATE_SLEEP_S)

    bar.close()
    return total, updated


# ─────────────────────────────────────────────────────────────────────────────
# items.title 기반 fix (non-arxiv)
# ─────────────────────────────────────────────────────────────────────────────

async def _fix_titles_from_items(limit: int, dry_run: bool) -> tuple[int, int]:
    """arxiv 제외 wiki_pages 중 bad title 인 것 — 그 page 의 items.title 중 가장 좋은
    것으로 fix. URL/slug 가 아닌 + 길이 충분.
    """
    session_factory = get_session_factory()
    async with session_factory() as session:
        async with session.begin():
            # wiki_page 마다 best item.title 한 줄 — confidence DESC, title 길이 DESC
            rows = (await session.execute(text("""
                SELECT
                    wp.id, wp.slug, wp.title AS wp_title,
                    (SELECT i.title
                       FROM wiki_page_items wpi
                       JOIN items i ON i.id = wpi.item_id
                       WHERE wpi.wiki_page_id = wp.id
                         AND (wpi.user_action IS NULL OR wpi.user_action != 'removed')
                         AND i.title IS NOT NULL
                         AND i.title NOT LIKE 'http%'
                         AND LENGTH(i.title) > 10
                       ORDER BY wpi.confidence DESC, LENGTH(i.title) DESC
                       LIMIT 1
                    ) AS best_title
                FROM wiki_pages wp
                WHERE wp.slug NOT LIKE 'arxiv__%'
                  AND (wp.title LIKE 'http%' OR wp.title LIKE 'arxiv:%'
                       OR wp.title LIKE 'doi:%' OR wp.title LIKE 'ytpl?:%'
                       OR wp.title LIKE 'github:%' OR wp.title = wp.slug)
                LIMIT :lim
            """), {"lim": limit})).mappings().all()

    candidates = [r for r in rows if r["best_title"]]
    total = len(rows)
    if not candidates:
        print(f"✅ items.title fallback 대상 없음 ({total} pages 검사, 좋은 item.title 없음)")
        return total, 0

    print(f"🔧 items.title fallback — {len(candidates)} / {total} pages 갱신 가능")
    if dry_run:
        for r in candidates[:10]:
            print(f"  • {r['slug']}: '{r['wp_title']}' → '{(r['best_title'] or '')[:60]}'")
        print("--- DRY RUN — 처리 안 함 ---")
        return total, 0

    updated = 0
    bar = tqdm(total=len(candidates), desc="🔧 items.title UPDATE", unit="page", mininterval=0.3)
    async with session_factory() as session:
        async with session.begin():
            for r in candidates:
                await session.execute(text(
                    "UPDATE wiki_pages SET title = :t WHERE id = :pid"
                ), {"t": r["best_title"], "pid": str(r["id"])})
                updated += 1
                bar.update(1)
    bar.close()
    return total, updated


# ─────────────────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────────────────

async def main(only_arxiv: bool, only_items: bool, limit: int, dry_run: bool) -> None:
    print(f"\n🔍 wiki_pages title fix — dry_run={dry_run} limit={limit}")

    if not only_items:
        a_total, a_upd = await _fix_arxiv_titles(limit, dry_run)
        print(f"  arxiv: {a_total} 검사, {a_upd} 갱신\n")

    if not only_arxiv:
        i_total, i_upd = await _fix_titles_from_items(limit, dry_run)
        print(f"  items.title fallback: {i_total} 검사, {i_upd} 갱신\n")

    # body_status='pending' 마킹 (title 바꿨으니 wiki body 재합성 필요)
    if not dry_run:
        async with get_session_factory()() as session:
            async with session.begin():
                affected = (await session.execute(text("""
                    UPDATE wiki_pages SET body_status = 'pending'
                    WHERE body_status = 'completed'
                    RETURNING 1
                """))).rowcount
        print(f"♻️  body_status='completed' → 'pending' ({affected} pages) — wiki_writer_worker 가 자동 재합성")

    await close_engine()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--only-arxiv", action="store_true", help="arxiv API 만 (items.title skip)")
    ap.add_argument("--only-items", action="store_true", help="items.title 만 (arxiv API skip)")
    ap.add_argument("--limit", type=int, default=100000, help="처리할 최대 갯수")
    ap.add_argument("--dry-run", action="store_true", help="처리 안 함")
    args = ap.parse_args()
    asyncio.run(main(
        only_arxiv=args.only_arxiv,
        only_items=args.only_items,
        limit=args.limit,
        dry_run=args.dry_run,
    ))
