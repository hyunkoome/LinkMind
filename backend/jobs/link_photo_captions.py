"""
photo_ 캡션 역추적 → 관련 위키에 figure 소스로 연결 (2026-05-29, Option B).

배경: 텔레그램에서 '사진 + URL 캡션' 한 메시지를 보내면 현재 ingest 가 사진(document
item + self_wiki) 과 URL(별도 item) 로 쪼갠다. 사진은 그 URL 콘텐츠의 figure(스크린샷)
인데 고아 photo 위키로 떠다님.

이 job: 각 photo item 의 텔레그램 caption(URL) 을 역추적 →
  caption → extract_external_ids → primary → sanitize_wiki_slug → 그 URL 의 위키
  (예: youtu.be/X → yt__x, github.com/o/r → github__o-r). external_id 없는 plain url
  은 그 url item 의 self_wiki.
대상 위키를 찾으면:
  1) photo item 을 그 위키에 'figure' 소스로 link (wiki_page_items).
  2) photo 자체 self_wiki (url__item__<photo>) 삭제 (Qdrant + Postgres).
photo item·이미지 파일은 보존 (raw-first §2, sVLL 학습 데이터). 단독 위키만 제거.

caption 없음/텍스트 caption 또는 대상 위키 못 찾으면 그대로 둠.

사용:
  python -m backend.jobs.link_photo_captions --dry-run
  python -m backend.jobs.link_photo_captions
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
import time

from sqlalchemy import text
from tqdm import tqdm

from backend.db.connection import close_engine, get_session_factory
from backend.embedding.wiki_qdrant import delete_wiki_page
from backend.utils.external_ids import extract_external_ids, primary_external_id
from backend.utils.wiki_slug import sanitize_wiki_slug

_LINK_SQL = text("""
    INSERT INTO wiki_page_items (wiki_page_id, item_id, confidence, source, role)
    VALUES (:wiki_id, :item_id, 1.0, 'auto', 'figure')
    ON CONFLICT (wiki_page_id, item_id) DO UPDATE
        SET role = COALESCE(wiki_page_items.role, EXCLUDED.role)
""")
_DELETE_WIKI_SQL = text("DELETE FROM wiki_pages WHERE id = :id")
# 재합성은 trigger 안 함 — 사진은 Sources 패널에 inline 즉시 표시되고(이미지),
# 본문은 OCR 없으면 사진 내용 못 쓰므로 1,575 재합성은 낭비. 필요 시 개별 재합성.


# caption 어디서든 첫 http(s) URL 추출 (맨 앞 아니어도 — '텍스트\n\nURL' 케이스).
_URL_RE = re.compile(r"https?://[^\s<>\"']+")


def _first_url(caption: str | None) -> str | None:
    if not caption:
        return None
    m = _URL_RE.search(caption)
    if not m:
        return None
    # trailing 구두점 strip
    return m.group(0).rstrip(").,」』]>)")


def _is_url(s: str | None) -> bool:
    """caption 에 URL 이 (어디든) 있나 — 통계/필터용."""
    return _first_url(s) is not None


def _target_slug(caption: str, source_url_to_item: dict[str, str]) -> str | None:
    """caption 의 URL → 연결할 대상 위키 slug. 못 정하면 None.

    external_id 있으면 sanitize(primary.slug) (yt__/github__/arxiv__ 등).
    없으면(plain url) 그 url 로 ingest 된 item 의 self_wiki (url__item__<uuid>).
    """
    url = _first_url(caption)
    if not url:
        return None
    ext = extract_external_ids(url=url, text=None)
    primary = primary_external_id(ext)
    if primary is not None:
        return sanitize_wiki_slug(primary.slug)
    # plain url — 같은 source_url 로 ingest 된 item 의 self_wiki
    item_id = source_url_to_item.get(url)
    if item_id:
        return sanitize_wiki_slug(f"url:item:{item_id}")
    return None


async def _build(session) -> dict:
    wiki_rows = (await session.execute(text(
        "SELECT id, slug FROM wiki_pages"
    ))).mappings().all()
    slug_to_wiki = {r["slug"]: str(r["id"]) for r in wiki_rows}

    # plain-url caption 매칭용 (source_url → item_id). photo 가 아닌 것만.
    src_rows = (await session.execute(text(
        "SELECT id, source_url FROM items WHERE source_url IS NOT NULL AND source_url <> ''"
    ))).mappings().all()
    source_url_to_item = {r["source_url"]: str(r["id"]) for r in src_rows}

    photo_rows = (await session.execute(text("""
        SELECT id, source_metadata->'telegram'->>'caption' AS caption
        FROM items WHERE title LIKE 'photo\\_%'
    """))).mappings().all()
    return {
        "slug_to_wiki": slug_to_wiki,
        "source_url_to_item": source_url_to_item,
        "photos": [(str(r["id"]), r["caption"]) for r in photo_rows],
    }


def _plan(maps: dict) -> tuple[list[dict], dict[str, int]]:
    """연결 계획 + 분류 통계."""
    plan: list[dict] = []
    stats = {"url_caption": 0, "no_url": 0, "target_found": 0, "no_target": 0,
             "self_collision": 0, "no_self_wiki": 0}
    for photo_id, caption in maps["photos"]:
        if not _is_url(caption):
            stats["no_url"] += 1
            continue
        stats["url_caption"] += 1
        target_slug = _target_slug(caption, maps["source_url_to_item"])
        target_wiki = maps["slug_to_wiki"].get(target_slug) if target_slug else None
        if not target_wiki:
            stats["no_target"] += 1
            continue
        self_slug = sanitize_wiki_slug(f"url:item:{photo_id}")
        self_wiki = maps["slug_to_wiki"].get(self_slug)
        if target_wiki == self_wiki:
            stats["self_collision"] += 1
            continue
        stats["target_found"] += 1
        if not self_wiki:
            stats["no_self_wiki"] += 1
        plan.append({
            "photo_id": photo_id, "target_wiki": target_wiki,
            "self_wiki": self_wiki, "target_slug": target_slug,
        })
    return plan, stats


async def main(dry_run: bool, limit: int | None) -> None:
    Session = get_session_factory()
    print("=" * 72, flush=True)
    print(f"link_photo_captions — dry_run={dry_run} limit={limit or '전체'}", flush=True)
    print("=" * 72, flush=True)

    async with Session() as s:
        maps = await _build(s)
    plan, stats = _plan(maps)
    if limit:
        plan = plan[:limit]

    print(f"\nphoto item: {len(maps['photos'])}", flush=True)
    print(f"  URL caption: {stats['url_caption']} / 비-URL(텍스트·없음): {stats['no_url']}",
          flush=True)
    print(f"  대상 위키 찾음(연결+self삭제): {stats['target_found']}", flush=True)
    print(f"  대상 못 찾음(그대로 둠): {stats['no_target']} / self충돌: {stats['self_collision']}",
          flush=True)
    if plan:
        print("\n  [연결 샘플 8]", flush=True)
        for p in plan[:8]:
            print(f"    photo {p['photo_id'][:8]} → {p['target_slug']}", flush=True)

    if dry_run:
        print("\n✅ DRY RUN — 변경 없음. 실제: --dry-run 빼고 재호출", flush=True)
        return

    start = time.monotonic()
    linked = deleted = errors = 0
    for p in tqdm(plan, desc="🖼 photo→위키 연결", unit="photo", mininterval=0.5):
        async with Session() as session:
            try:
                # 1) 대상 위키에 figure 소스로 link (Sources 패널에 이미지 inline 표시)
                await session.execute(_LINK_SQL,
                                      {"wiki_id": p["target_wiki"], "item_id": p["photo_id"]})
                # 2) photo 단독 self_wiki 삭제 (있으면)
                if p["self_wiki"]:
                    try:
                        await delete_wiki_page(p["self_wiki"])
                    except Exception:  # noqa: BLE001
                        pass
                    await session.execute(_DELETE_WIKI_SQL, {"id": p["self_wiki"]})
                    deleted += 1
                await session.commit()
                linked += 1
            except Exception as exc:  # noqa: BLE001
                await session.rollback()
                errors += 1
                tqdm.write(f"⚠ 실패 (photo={p['photo_id']}): {exc}")

    print("\n" + "=" * 72, flush=True)
    print(f"✅ 완료 — 연결 {linked}, photo 위키 삭제 {deleted}, errors {errors} "
          f"({time.monotonic() - start:.0f}s)", flush=True)


async def _entry(dry_run: bool, limit: int | None) -> None:
    try:
        await main(dry_run, limit)
    finally:
        await close_engine()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="photo 캡션 역추적 → 위키 figure 연결")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    asyncio.run(_entry(args.dry_run, args.limit))
    sys.stdout.flush()
