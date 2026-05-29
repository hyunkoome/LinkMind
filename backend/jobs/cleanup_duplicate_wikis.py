"""
D10.6 B (2026-05-29) — wiki 중복 정리 (native-identity 기준).

D10.6 A/A2 가 '생성측' 을 고쳐 앞으로는 1 링크 = 1 위키. 이 job 은 그 전에 쌓인
중복 wiki 를 정리한다. 옛 `merge_duplicate_wikis.py` 의 title 기준(min_len=20 →
0 group, 무용) 대신 **자료 자신의 URL 에서 파생된 정체성** 기준으로 묶는다.

핵심 개념 — '정체성(native identity)':
  item 의 native 정체성 = extract_external_ids(url=source_url) (URL 에서만, 본문 X).
  youtube → yt__<id>, github → github__<owner>-<repo>, arxiv URL → arxiv__<id>,
  일반 블로그 → 없음. 이건 topic confidence (옛 primary_external_id 랭킹 버그로
  뒤바뀜) 와 무관하게 robust 하다.

두 가지 정리 (둘 다 item_topics 그래프 관계는 보존 — 별 테이블, CASCADE 아님):

  T1 — self_wiki 중복 merge:
    `url__item__<uuid>` self_wiki 가 있고 그 자료가 native 외부 wiki 도 가지면
    (예: youtube 자료가 yt__ + url__item__ 둘 다), self_wiki 를 native wiki 로
    merge (wiki_page_items 이동 + keywords union + native 가 body 비었으면 self
    body 흡수) 후 self_wiki 삭제. self_wiki 는 그 자료 전용이라 cross-item 위험 0.

  T2 — 순수 clue 아티팩트 삭제:
    yt__/github__/arxiv__/doi__/ytpl__ prefix wiki 인데 **아무 item 도 native 로
    소유 안 함** (= 자기 URL 로 이 자료를 직접 ingest 한 적 없음, 콘텐츠에서 언급만).
    유령 페이지(예: 블로그가 언급한 github__tensorflow-tensorflow) + 텍스트 오추출
    garbage(예: arxiv__2604.00025) → wiki_page + Qdrant point 삭제. 사용자가 그
    자료를 직접 보내면 그때 진짜 wiki 가 생김.

  ※ LLM new_pages 가 만든 주제 wiki (kebab-case slug, 외부 prefix 아님) 는 T2 대상
    아님 — 보존.

사용:
  python -m backend.jobs.cleanup_duplicate_wikis --dry-run       # 수치 + 샘플만
  python -m backend.jobs.cleanup_duplicate_wikis --t1-only       # self_wiki merge 만
  python -m backend.jobs.cleanup_duplicate_wikis --t2-only       # phantom 삭제만
  python -m backend.jobs.cleanup_duplicate_wikis                 # T1 + T2 실제 실행
  python -m backend.jobs.cleanup_duplicate_wikis --limit 50      # 디버깅 (각 50건)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from collections import defaultdict
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from tqdm import tqdm

from backend.db.connection import close_engine, get_session_factory
from backend.embedding.wiki_qdrant import delete_wiki_page
from backend.utils.external_ids import extract_external_ids, native_identity_external_id
from backend.utils.wiki_slug import sanitize_wiki_slug

logging.basicConfig(level=logging.WARNING,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("linkmind.jobs.cleanup_duplicate_wikis")

# native identity 가 될 수 있는 외부 prefix (sanitize_wiki_slug 후 '__' 형태).
_EXTERNAL_PREFIXES = ("yt__", "github__", "arxiv__", "doi__", "ytpl__")
_SELF_WIKI_PREFIX = "url__item__"

# source_type → native 외부 kind (T1 target 선택 우선순위용).
_NATIVE_KIND_BY_SOURCE = {
    "youtube": "yt", "youtube_playlist": "ytpl", "github": "github",
}


# ─────────────────────────────────────────────────────────────────────────────
# SQL
# ─────────────────────────────────────────────────────────────────────────────

_MOVE_WIKI_PAGE_ITEMS_SQL = text("""
    INSERT INTO wiki_page_items (
        wiki_page_id, item_id, confidence, source, role,
        user_action, user_action_at, created_at
    )
    SELECT :target_pid, item_id, confidence, source, role,
           user_action, user_action_at, created_at
    FROM wiki_page_items
    WHERE wiki_page_id = :source_pid
    ON CONFLICT (wiki_page_id, item_id) DO UPDATE
        SET confidence = GREATEST(wiki_page_items.confidence, EXCLUDED.confidence),
            user_action = COALESCE(wiki_page_items.user_action, EXCLUDED.user_action),
            user_action_at = COALESCE(wiki_page_items.user_action_at, EXCLUDED.user_action_at),
            role = COALESCE(wiki_page_items.role, EXCLUDED.role)
""")

_UPDATE_KEYWORDS_SQL = text("""
    UPDATE wiki_pages
    SET keywords = (
        SELECT ARRAY(
            SELECT DISTINCT kw FROM UNNEST(CAST(:merged AS text[])) AS kw
            WHERE kw IS NOT NULL AND TRIM(kw) <> ''
        )
    )
    WHERE id = :target_pid
""")

_ADOPT_BODY_SQL = text("""
    UPDATE wiki_pages
    SET body = :body, body_status = 'completed', body_generated_at = now(),
        body_model = :body_model, body_prompt_version = COALESCE(body_prompt_version, 'merged_self')
    WHERE id = :target_pid AND COALESCE(TRIM(body), '') = ''
""")

_INSERT_VERSION_SQL = text("""
    INSERT INTO wiki_page_versions (
        page_id, version_number, body, body_model, body_prompt_version, trigger_reason
    )
    VALUES (
        :page_id,
        COALESCE((SELECT MAX(version_number) FROM wiki_page_versions WHERE page_id = :page_id), 0) + 1,
        :body, :body_model, :body_prompt_version, 'cleanup_merge_self'
    )
    ON CONFLICT (page_id, version_number) DO NOTHING
""")

_DELETE_WIKI_SQL = text("DELETE FROM wiki_pages WHERE id = :page_id")


# ── T3: orphan rehome (wiki 0개 item 에 정체성 wiki 보장) ──
# idempotent §2 로 DB item 은 전부 비중복 — 따라서 모든 item 은 정체성 wiki 를
# 가져야 한다 (사용자 원칙 2026-05-29). cleanup 이 phantom 을 지우며 self_wiki 를
# 안 만들어준 orphan 을 복구. body_status='pending' → daemon 자동 합성.
_FETCH_ORPHANS_SQL = text("""
    SELECT i.id, i.source_type, i.source_url, i.title, i.summary
    FROM items i
    WHERE NOT EXISTS (SELECT 1 FROM wiki_page_items w WHERE w.item_id = i.id)
""")

_CREATE_IDENTITY_WIKI_SQL = text("""
    INSERT INTO wiki_pages (slug, title, description, body_status)
    VALUES (:slug, :title, :description, 'pending')
    ON CONFLICT (slug) DO UPDATE SET slug = EXCLUDED.slug
    RETURNING id
""")

_LINK_IDENTITY_WIKI_SQL = text("""
    INSERT INTO wiki_page_items (wiki_page_id, item_id, confidence, source, role)
    VALUES (:page_id, :item_id, 1.0, 'auto', :role)
    ON CONFLICT (wiki_page_id, item_id) DO NOTHING
""")


def _orphan_identity(source_type: str, source_url: str | None, item_id: str) -> tuple[str, str]:
    """orphan item 의 정체성 wiki (slug, role) — A2 native_identity_external_id 재사용.

    자기 URL/타입에서 native id 가 나오면 그 slug (role='primary'), 없으면
    url:item:<uuid> self_wiki (role='self'). 콘텐츠 추출 id 는 정체성 아님 (D10.6 A2).
    """
    nids = extract_external_ids(url=source_url, text=None) if source_url else []
    native = native_identity_external_id(source_type=source_type, url=source_url, ids=nids)
    if native is not None:
        return sanitize_wiki_slug(native.slug), "primary"
    return sanitize_wiki_slug(f"url:item:{item_id}"), "self"


# ─────────────────────────────────────────────────────────────────────────────
# Phase 0 — native identity 맵 구성
# ─────────────────────────────────────────────────────────────────────────────

async def _build_maps(session: AsyncSession) -> dict[str, Any]:
    wiki_rows = (await session.execute(text(
        "SELECT id, slug, title, body, body_status, body_model, keywords FROM wiki_pages"
    ))).mappings().all()
    slug_to_wiki = {r["slug"]: str(r["id"]) for r in wiki_rows}
    wiki_by_id = {str(r["id"]): dict(r) for r in wiki_rows}

    item_rows = (await session.execute(text(
        "SELECT id, source_type, source_url FROM items"
    ))).mappings().all()

    # item → native slugs (자기 URL 에서만) + 전체 natively-owned slug 집합
    item_native_slugs: dict[str, set[str]] = {}
    item_source_type: dict[str, str] = {}
    natively_owned: set[str] = set()
    for r in item_rows:
        iid = str(r["id"])
        item_source_type[iid] = r["source_type"]
        nids = extract_external_ids(url=r["source_url"], text=None) if r["source_url"] else []
        nslugs = {sanitize_wiki_slug(n.slug) for n in nids}
        item_native_slugs[iid] = nslugs
        natively_owned |= nslugs

    # item → linked wiki ids
    item_wikis: dict[str, set[str]] = defaultdict(set)
    for r in (await session.execute(text(
        "SELECT item_id, wiki_page_id FROM wiki_page_items"
    ))).mappings().all():
        item_wikis[str(r["item_id"])].add(str(r["wiki_page_id"]))

    return {
        "slug_to_wiki": slug_to_wiki,
        "wiki_by_id": wiki_by_id,
        "item_native_slugs": item_native_slugs,
        "item_source_type": item_source_type,
        "natively_owned": natively_owned,
        "item_wikis": item_wikis,
    }


def _pick_t1_target(item_id: str, native_wiki_ids: list[str], maps: dict) -> str:
    """T1 target — 자료의 source_type 에 맞는 native wiki 우선, 없으면 첫 번째."""
    if len(native_wiki_ids) == 1:
        return native_wiki_ids[0]
    native_kind = _NATIVE_KIND_BY_SOURCE.get(maps["item_source_type"].get(item_id, ""))
    if native_kind:
        prefix = native_kind + "__"
        for wid in native_wiki_ids:
            if maps["wiki_by_id"][wid]["slug"].startswith(prefix):
                return wid
    # tie-break: 외부 prefix 우선, 그 안에서 slug 사전순 (결정적)
    return sorted(native_wiki_ids, key=lambda w: maps["wiki_by_id"][w]["slug"])[0]


def _detect_t1(maps: dict) -> list[dict[str, Any]]:
    """T1 — self_wiki(url__item__<uuid>) + native 외부 wiki 동시 보유 자료."""
    plan = []
    for item_id, linked in maps["item_wikis"].items():
        self_slug = sanitize_wiki_slug(f"url:item:{item_id}")
        self_wiki = maps["slug_to_wiki"].get(self_slug)
        if not self_wiki or self_wiki not in linked:
            continue
        native_ids = [
            maps["slug_to_wiki"][s] for s in maps["item_native_slugs"].get(item_id, set())
            if s in maps["slug_to_wiki"] and maps["slug_to_wiki"][s] in linked
        ]
        if not native_ids:
            continue  # self_wiki 가 유일한 정체성 → 정상, merge 안 함
        target = _pick_t1_target(item_id, native_ids, maps)
        plan.append({"item_id": item_id, "self_wiki": self_wiki, "target": target})
    return plan


def _detect_t2(maps: dict) -> list[str]:
    """T2 — 외부 prefix wiki 인데 아무 item 도 native 로 소유 안 함 (유령).

    T1 의 self_wiki (url__item__ prefix) 와는 prefix 상 disjoint 라 겹칠 일 없음.
    """
    out = []
    for wid, w in maps["wiki_by_id"].items():
        slug = w["slug"]
        if not slug.startswith(_EXTERNAL_PREFIXES):
            continue
        if slug in maps["natively_owned"]:
            continue  # 누군가 자기 URL 로 직접 ingest → 진짜 자료
        out.append(wid)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 실행 — T1 merge / T2 delete (각 단위 별 session)
# ─────────────────────────────────────────────────────────────────────────────

async def _merge_self_into_target(session: AsyncSession, *, self_wiki: str,
                                   target: str, maps: dict) -> None:
    tgt = maps["wiki_by_id"][target]
    src = maps["wiki_by_id"][self_wiki]
    # 1) wiki_page_items 이동
    await session.execute(_MOVE_WIKI_PAGE_ITEMS_SQL,
                          {"target_pid": target, "source_pid": self_wiki})
    # 2) keywords union
    merged = list(dict.fromkeys(list(tgt["keywords"] or []) + list(src["keywords"] or [])))
    if merged:
        await session.execute(_UPDATE_KEYWORDS_SQL, {"target_pid": target, "merged": merged})
    # 3) target 이 body 비었고 self 에 body 있으면 흡수 (+version)
    src_body = (src["body"] or "").strip()
    if src_body and not (tgt["body"] or "").strip():
        model = src.get("body_model") or "inherited"
        await session.execute(_INSERT_VERSION_SQL, {
            "page_id": target, "body": src_body, "body_model": model,
            "body_prompt_version": "merged_self",
        })
        await session.execute(_ADOPT_BODY_SQL,
                              {"target_pid": target, "body": src_body, "body_model": model})
    # 4) self_wiki 삭제 (Qdrant + Postgres CASCADE)
    try:
        await delete_wiki_page(self_wiki)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Qdrant delete 실패 (self=%s): %s — 계속", src["slug"], exc)
    await session.execute(_DELETE_WIKI_SQL, {"page_id": self_wiki})


async def _delete_phantom(session: AsyncSession, *, wiki_id: str, maps: dict) -> None:
    try:
        await delete_wiki_page(wiki_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Qdrant delete 실패 (phantom=%s): %s — 계속",
                       maps["wiki_by_id"][wiki_id]["slug"], exc)
    await session.execute(_DELETE_WIKI_SQL, {"page_id": wiki_id})


async def _rehome_orphan(session: AsyncSession, *, orphan: dict) -> None:
    """orphan item 에 정체성 wiki 보장 (없으면 생성) + link. body_status='pending'."""
    slug, role = _orphan_identity(
        orphan["source_type"], orphan["source_url"], str(orphan["id"]))
    title = orphan["title"] or slug
    description = (orphan["summary"] or "")[:500] or None
    row = (await session.execute(_CREATE_IDENTITY_WIKI_SQL, {
        "slug": slug, "title": title, "description": description,
    })).first()
    if not row:
        return
    await session.execute(_LINK_IDENTITY_WIKI_SQL, {
        "page_id": str(row[0]), "item_id": str(orphan["id"]), "role": role,
    })


# ─────────────────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────────────────

async def _run_rehome(Session, dry_run: bool, limit: int | None) -> None:
    """T3 — wiki 0개 orphan item 에 정체성 wiki 보장 (additive, 삭제 아님)."""
    async with Session() as s:
        orphans = [dict(r) for r in (await s.execute(_FETCH_ORPHANS_SQL)).mappings().all()]
    if limit:
        orphans = orphans[:limit]

    # text 유무 분류 (이미지 = summary 없음, 합성하면 thin body)
    with_text = [o for o in orphans if (o["summary"] or "").strip()]
    no_text = [o for o in orphans if not (o["summary"] or "").strip()]
    by_type: dict[str, int] = {}
    for o in orphans:
        by_type[o["source_type"]] = by_type.get(o["source_type"], 0) + 1

    print(f"\norphan item (wiki 0개): {len(orphans)}", flush=True)
    print(f"  source_type 별: {by_type}", flush=True)
    print(f"  summary 있음(합성 가능)={len(with_text)}  없음(이미지 등)={len(no_text)}",
          flush=True)
    if orphans:
        print("\n  [샘플 6]", flush=True)
        for o in orphans[:6]:
            slug, role = _orphan_identity(o["source_type"], o["source_url"], str(o["id"]))
            print(f"    {o['source_type']:9} {(o['title'] or '')[:30]:32} → {slug[:40]} ({role})",
                  flush=True)

    if dry_run:
        print("\n✅ DRY RUN — 변경 없음. 실제 실행: --rehome-orphans (--dry-run 빼고)",
              flush=True)
        return

    start = time.monotonic()
    done, errors = 0, 0
    for o in tqdm(orphans, desc="🏠 orphan 정체성 wiki 생성", unit="item", mininterval=0.5):
        async with Session() as session:
            try:
                await _rehome_orphan(session, orphan=o)
                await session.commit()
                done += 1
            except Exception as exc:  # noqa: BLE001
                await session.rollback()
                errors += 1
                tqdm.write(f"⚠ rehome 실패 (item={o['id']}): {exc}")
    print("\n" + "=" * 72, flush=True)
    print(f"✅ rehome 완료 — {done} item 정체성 wiki 생성/link (pending), errors={errors} "
          f"({time.monotonic() - start:.0f}s). daemon 이 자동 합성.", flush=True)


async def main(dry_run: bool, do_t1: bool, do_t2: bool, do_rehome: bool,
               limit: int | None) -> None:
    Session = get_session_factory()
    print("=" * 72, flush=True)
    print(f"cleanup_duplicate_wikis — dry_run={dry_run} t1={do_t1} t2={do_t2} "
          f"rehome={do_rehome} limit={limit or '전체'}", flush=True)
    print("=" * 72, flush=True)

    # T3 rehome 는 독립 모드 (cleanup 부작용 복구) — 단독 실행.
    if do_rehome:
        await _run_rehome(Session, dry_run, limit)
        return

    async with Session() as s:
        maps = await _build_maps(s)

    t1_plan = _detect_t1(maps) if do_t1 else []
    t2_plan = _detect_t2(maps) if do_t2 else []
    if limit:
        t1_plan, t2_plan = t1_plan[:limit], t2_plan[:limit]

    print(f"\n총 wiki={len(maps['wiki_by_id'])}  "
          f"native-owned slug={len(maps['natively_owned'])}", flush=True)
    print(f"T1 (self_wiki merge): {len(t1_plan)}", flush=True)
    print(f"T2 (phantom 삭제)    : {len(t2_plan)}", flush=True)

    # 샘플 출력 (항상 — dry-run 이든 실제든)
    if t1_plan:
        print("\n  [T1 샘플 5]", flush=True)
        for p in t1_plan[:5]:
            print(f"    {maps['wiki_by_id'][p['self_wiki']]['slug'][:48]}"
                  f"  →  {maps['wiki_by_id'][p['target']]['slug']}", flush=True)
    if t2_plan:
        print("\n  [T2 샘플 8]", flush=True)
        for wid in t2_plan[:8]:
            w = maps["wiki_by_id"][wid]
            print(f"    {w['slug'][:55]}  (body_status={w['body_status']})", flush=True)

    if dry_run:
        print("\n✅ DRY RUN — 변경 없음. 실제 실행: --dry-run 빼고 재호출", flush=True)
        return

    start = time.monotonic()
    stats = {"t1_merged": 0, "t2_deleted": 0, "errors": 0}

    # T1 실행
    for p in tqdm(t1_plan, desc="🔗 T1 self_wiki merge", unit="wiki",
                  mininterval=0.5, disable=not t1_plan):
        async with Session() as session:
            try:
                await _merge_self_into_target(
                    session, self_wiki=p["self_wiki"], target=p["target"], maps=maps)
                await session.commit()
                stats["t1_merged"] += 1
            except Exception as exc:  # noqa: BLE001
                await session.rollback()
                stats["errors"] += 1
                tqdm.write(f"⚠ T1 실패 (self={p['self_wiki']}): {exc}")

    # T2 실행
    for wid in tqdm(t2_plan, desc="🗑 T2 phantom 삭제", unit="wiki",
                    mininterval=0.5, disable=not t2_plan):
        async with Session() as session:
            try:
                await _delete_phantom(session, wiki_id=wid, maps=maps)
                await session.commit()
                stats["t2_deleted"] += 1
            except Exception as exc:  # noqa: BLE001
                await session.rollback()
                stats["errors"] += 1
                tqdm.write(f"⚠ T2 실패 (wiki={wid}): {exc}")

    elapsed = time.monotonic() - start
    print("\n" + "=" * 72, flush=True)
    print(f"✅ 완료 — T1 merged={stats['t1_merged']}, T2 deleted={stats['t2_deleted']}, "
          f"errors={stats['errors']}  ({elapsed:.0f}s)", flush=True)


async def _entry(dry_run: bool, do_t1: bool, do_t2: bool, do_rehome: bool,
                 limit: int | None) -> None:
    try:
        await main(dry_run, do_t1, do_t2, do_rehome, limit)
    finally:
        await close_engine()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="native-identity 기준 wiki 중복 정리")
    parser.add_argument("--dry-run", action="store_true", help="수치+샘플만, 변경 X")
    parser.add_argument("--t1-only", action="store_true", help="self_wiki merge 만")
    parser.add_argument("--t2-only", action="store_true", help="phantom 삭제만")
    parser.add_argument("--rehome-orphans", action="store_true",
                        help="wiki 0개 orphan item 에 정체성 wiki 생성 (additive, 단독 모드)")
    parser.add_argument("--limit", type=int, default=None, help="각 단계 N건만 (디버깅)")
    args = parser.parse_args()

    do_t1 = not args.t2_only
    do_t2 = not args.t1_only
    asyncio.run(_entry(args.dry_run, do_t1, do_t2, args.rehome_orphans, args.limit))
    sys.stdout.flush()
