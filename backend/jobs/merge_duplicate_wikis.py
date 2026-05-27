"""
D10.5 (2026-05-27) — 같은 source title 의 wiki 들을 1 wiki 로 자동 merge.

배경:
  - D11 의 classifier ca431aa 가 self_wiki 무조건 INSERT — 외부 ID wiki + self_wiki
    동시 존재 (6,625 items).
  - 또 같은 콘텐츠 (예: "Quark: Real-time, High-resolution...") 가 multi modality
    (slack + 프로젝트 + arxiv + pdf) 로 들어오면 wiki 가 5-7개 분열.
  - 사용자 mental model: 같은 source title = 같은 주제 = wiki 1개.

설계:
  1. discovery — normalized title 동일 + 길이 >= 20자 + non-placeholder 인
     wiki group 찾기.
  2. 각 group:
     a) 대표 wiki 선택 — external_id slug (yt__/github__/arxiv__/doi__/ytpl__)
        우선, 같은 외부 ID 여러개면 가장 오래된 (first created), 모두 self_wiki
        면 가장 최신 updated_at.
     b) 병합 대상의 wiki_page_items → 대표로 옮김 (UPSERT, max confidence).
     c) keywords union (중복 제거).
     d) body merge:
        - 둘 다 비어있음 → skip (옛것 delete, 대표는 옛 상태 유지)
        - 한쪽만 body 있음 → 대표가 비어있으면 그쪽 body 로 update
        - 둘 다 body 있고 둘 다 'completed' → vLLM merge → new version 적립
     e) 병합 wiki Qdrant point delete + Postgres DELETE (CASCADE).

대표 정책 — raw 보존 (§2 raw-first):
  - items 는 절대 안 건드림 (모든 raw + user_notes + 첨부 보존).
  - wiki_page_items 의 user_action='kept'/'pinned' 도 보존.
  - 병합 대상 wiki 의 wiki_page_versions 는 CASCADE 로 사라지지만 (학습 신호 손실)
    이 wiki 자체가 잉여라 OK — 대표 wiki 의 새 version 에 통합 body 적립.

idempotent: 이미 merge 된 group 은 카운트 == 1 이라 discovery 에서 안 잡힘.

사용:
  python -m backend.jobs.merge_duplicate_wikis --dry-run     # 진단만
  python -m backend.jobs.merge_duplicate_wikis --limit 10    # 10 group 만 실행
  python -m backend.jobs.merge_duplicate_wikis               # 전체 (~수십분~수시간)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from tqdm import tqdm

from backend.agents.base import load_prompt
from backend.db.connection import close_engine, get_session_factory
from backend.embedding.wiki_qdrant import delete_wiki_page
from backend.llm.base import ChatMessage
from backend.llm.factory import get_llm_provider

# tqdm 출력 보호 — WARNING 이상만 stdout, INFO 는 무시 (진행 bar 가시성).
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("linkmind.jobs.merge_duplicate_wikis")


# ─────────────────────────────────────────────────────────────────────────────
# discovery — normalized title group
# ─────────────────────────────────────────────────────────────────────────────

# title placeholder (도메인 / 본문 추출 실패) — 의도 아닌 중복이라 merge 부적합.
# 도메인 패턴은 SQL ILIKE 로 분리 매칭.
_PLACEHOLDER_TITLES: tuple[str, ...] = (
    "Social Media Title Tag", "Abstract", "[no-title]", "paper_title",
    "untitled", "no title",
)
_MIN_TITLE_LEN = 20


_FIND_DUPLICATE_GROUPS_SQL = text("""
    SELECT LOWER(TRIM(title)) AS norm_title, COUNT(*) AS cnt
    FROM wiki_pages
    WHERE title IS NOT NULL
      AND LENGTH(TRIM(title)) >= :min_len
      AND LOWER(TRIM(title)) <> ALL(CAST(:placeholders AS text[]))
      AND TRIM(title) NOT ILIKE '%.com'
      AND TRIM(title) NOT ILIKE '%.co.kr'
      AND TRIM(title) NOT ILIKE '%.org'
      AND TRIM(title) NOT ILIKE '%.net'
      AND TRIM(title) NOT ILIKE '%.io'
      AND TRIM(title) NOT ILIKE 'www.%'
    GROUP BY LOWER(TRIM(title))
    HAVING COUNT(*) >= 2
    ORDER BY cnt DESC, norm_title
    LIMIT :limit
""")


_FETCH_GROUP_WIKIS_SQL = text("""
    SELECT id, slug, title, description, body, body_status, body_model,
           body_prompt_version, keywords, created_at, updated_at,
           (SELECT COUNT(*) FROM wiki_page_items wpi WHERE wpi.wiki_page_id = wp.id) AS source_count,
           (SELECT COALESCE(MAX(version_number), 0)
                FROM wiki_page_versions WHERE page_id = wp.id) AS latest_version
    FROM wiki_pages wp
    WHERE LOWER(TRIM(title)) = :norm_title
    ORDER BY created_at ASC
""")


# external_id slug prefix 들 (sanitize_wiki_slug 후 형태 — '__' 구분).
# self_wiki = 'url__item__<uuid>' (prefix `url__item__`).
_EXTERNAL_ID_PREFIXES = ("arxiv__", "github__", "yt__", "ytpl__", "doi__")
_SELF_WIKI_PREFIX = "url__item__"


def _is_external_id_wiki(slug: str) -> bool:
    return any(slug.startswith(p) for p in _EXTERNAL_ID_PREFIXES)


def _is_self_wiki(slug: str) -> bool:
    return slug.startswith(_SELF_WIKI_PREFIX)


def _pick_representative(wikis: list[dict[str, Any]]) -> dict[str, Any]:
    """대표 wiki 선택 정책.

    우선순위:
      1. external_id wiki (yt__/github__/arxiv__/doi__/ytpl__) > self_wiki
      2. 같은 카테고리 안에서 body_status='completed' 우선
      3. 같은 status 면 body 길이 큰 쪽 (정보량 많음)
      4. 같으면 가장 오래된 (first created)
    """
    def sort_key(w: dict[str, Any]) -> tuple[int, int, int, str]:
        # 낮을수록 우선 (sort ASC)
        prio_ext = 0 if _is_external_id_wiki(w["slug"]) else 1
        prio_completed = 0 if w["body_status"] == "completed" else 1
        # body 길이 — 큰 쪽 우선이라 음수
        body_len_neg = -(len(w["body"] or ""))
        # 가장 오래된 (UTC ISO string 으로 ASC)
        created = w["created_at"].isoformat() if w["created_at"] else ""
        return (prio_ext, prio_completed, body_len_neg, created)

    return sorted(wikis, key=sort_key)[0]


# ─────────────────────────────────────────────────────────────────────────────
# merge — 단일 group 처리
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
            -- user_action 우선 보존: 기존이 NULL 이면 EXCLUDED 가져옴.
            user_action = COALESCE(wiki_page_items.user_action, EXCLUDED.user_action),
            user_action_at = COALESCE(wiki_page_items.user_action_at, EXCLUDED.user_action_at),
            -- role 도 기존이 NULL 이면 EXCLUDED.
            role = COALESCE(wiki_page_items.role, EXCLUDED.role)
""")


_UPDATE_KEYWORDS_SQL = text("""
    UPDATE wiki_pages
    SET keywords = (
        SELECT ARRAY(
            SELECT DISTINCT kw
            FROM UNNEST(CAST(:merged_keywords AS text[])) AS kw
            WHERE kw IS NOT NULL AND TRIM(kw) <> ''
        )
    )
    WHERE id = :target_pid
""")


_UPDATE_BODY_SQL = text("""
    UPDATE wiki_pages
    SET body = :body,
        body_status = 'completed',
        body_generated_at = now(),
        body_model = :body_model,
        body_prompt_version = 'merger_v1'
    WHERE id = :target_pid
""")


_INSERT_VERSION_SQL = text("""
    INSERT INTO wiki_page_versions (
        page_id, version_number, body, body_model, body_prompt_version,
        trigger_reason
    )
    VALUES (:page_id, :version_number, :body, :body_model, :body_prompt_version,
            'merge_duplicate')
    ON CONFLICT (page_id, version_number) DO NOTHING
""")


_DELETE_WIKI_PAGE_SQL = text("""
    DELETE FROM wiki_pages WHERE id = :page_id
""")


async def _vllm_body_merge(
    *, title_a: str, body_a: str, title_b: str, body_b: str,
) -> tuple[str, str]:
    """vLLM 으로 두 wiki body 통합. (merged_body, model_name) 반환."""
    prompt = load_prompt("merger", "v1")
    user_msg = prompt["user_template"].format(
        title_a=title_a, body_a=body_a, title_b=title_b, body_b=body_b,
    )
    provider = get_llm_provider()
    resp = await provider.chat(
        messages=[
            ChatMessage(role="system", content=prompt["system"]),
            ChatMessage(role="user", content=user_msg),
        ],
        model=None,
        temperature=0.2,
        max_tokens=2048,
    )
    return resp.text.strip(), resp.model or "unknown"


async def merge_group(
    session: AsyncSession,
    *,
    norm_title: str,
    wikis: list[dict[str, Any]],
    dry_run: bool,
) -> dict[str, Any]:
    """norm_title 그룹의 wiki 들을 1개로 merge.

    Returns: {target_slug, merged_slugs, body_merged_via, ...} (요약 정보).
    """
    if len(wikis) < 2:
        return {"skipped": "single", "norm_title": norm_title[:50]}

    target = _pick_representative(wikis)
    sources = [w for w in wikis if w["id"] != target["id"]]

    summary: dict[str, Any] = {
        "norm_title": norm_title[:80],
        "target_slug": target["slug"],
        "target_status": target["body_status"],
        "merged_slugs": [s["slug"] for s in sources],
        "body_merged_via": None,  # 'kept' | 'adopted_from_source' | 'vllm_merged'
        "items_moved": 0,
        "keywords_added": 0,
    }

    if dry_run:
        # 실제 변경 X — 통계만 시뮬레이션 (사용자가 예상 규모 파악 가능).
        for src in sources:
            cnt_row = (await session.execute(
                text("SELECT COUNT(*) FROM wiki_page_items WHERE wiki_page_id = :pid"),
                {"pid": src["id"]},
            )).scalar() or 0
            summary["items_moved"] += int(cnt_row)
        target_kw = set(target["keywords"] or [])
        for src in sources:
            for kw in (src["keywords"] or []):
                if kw not in target_kw:
                    summary["keywords_added"] += 1
                    target_kw.add(kw)
        # body merge 분류 시뮬레이션
        target_body = (target["body"] or "").strip()
        best_src = max(sources, key=lambda s: len(s["body"] or ""))
        src_body = (best_src["body"] or "").strip()
        if not target_body and not src_body:
            summary["body_merged_via"] = "both_empty"
        elif not target_body and src_body:
            summary["body_merged_via"] = "adopted_from_source"
        elif target_body and not src_body:
            summary["body_merged_via"] = "kept_target"
        elif target["body_status"] == "completed" and best_src["body_status"] == "completed":
            summary["body_merged_via"] = "vllm_merged"
        else:
            summary["body_merged_via"] = "kept_target_status_diff"
        return summary

    # ──────── 1) wiki_page_items 옮김 (병합 wiki → 대표) ────────
    for src in sources:
        # 옮기기 전 row count
        cnt_row = (await session.execute(
            text("SELECT COUNT(*) FROM wiki_page_items WHERE wiki_page_id = :pid"),
            {"pid": src["id"]},
        )).scalar() or 0
        await session.execute(_MOVE_WIKI_PAGE_ITEMS_SQL, {
            "target_pid": target["id"],
            "source_pid": src["id"],
        })
        summary["items_moved"] += int(cnt_row)

    # ──────── 2) keywords union ────────
    all_keywords: list[str] = list(target["keywords"] or [])
    for src in sources:
        for kw in (src["keywords"] or []):
            if kw not in all_keywords:
                all_keywords.append(kw)
                summary["keywords_added"] += 1
    if summary["keywords_added"] > 0:
        await session.execute(_UPDATE_KEYWORDS_SQL, {
            "target_pid": target["id"],
            "merged_keywords": all_keywords,
        })

    # ──────── 3) body merge ────────
    target_body = (target["body"] or "").strip()
    # source 중 가장 긴 body
    best_src = max(sources, key=lambda s: len(s["body"] or ""))
    src_body = (best_src["body"] or "").strip()

    new_body: str | None = None
    new_body_model: str | None = None

    if not target_body and not src_body:
        # 둘 다 비어있음 — 대표는 그대로 (body_status 변경 X)
        summary["body_merged_via"] = "both_empty"
    elif not target_body and src_body:
        # 대표가 비어있고 source 에 body 있음 → 대표가 source body 흡수.
        new_body = src_body
        new_body_model = best_src.get("body_model") or "inherited"
        summary["body_merged_via"] = "adopted_from_source"
    elif target_body and not src_body:
        # 대표만 body 있음 → 대표 그대로
        summary["body_merged_via"] = "kept_target"
    else:
        # 둘 다 body 있음 — completed 충돌 케이스. vLLM merge.
        if target["body_status"] == "completed" and best_src["body_status"] == "completed":
            merged_body, model = await _vllm_body_merge(
                title_a=target["title"], body_a=target_body,
                title_b=best_src["title"], body_b=src_body,
            )
            new_body = merged_body
            new_body_model = f"merger:{model}"
            summary["body_merged_via"] = "vllm_merged"
        else:
            # 대표만 completed 면 대표 유지. 아니면 longest 자체 유지.
            summary["body_merged_via"] = "kept_target_status_diff"

    if new_body is not None:
        # 새 version 적립 후 body update
        new_version = int(target["latest_version"] or 0) + 1
        await session.execute(_INSERT_VERSION_SQL, {
            "page_id": target["id"],
            "version_number": new_version,
            "body": new_body,
            "body_model": new_body_model or "merger",
            "body_prompt_version": "merger_v1",
        })
        await session.execute(_UPDATE_BODY_SQL, {
            "target_pid": target["id"],
            "body": new_body,
            "body_model": new_body_model or "merger",
        })

    # ──────── 4) 병합 wiki Qdrant + Postgres delete ────────
    for src in sources:
        try:
            await delete_wiki_page(str(src["id"]))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Qdrant delete 실패 (slug=%s, %s) — 계속 진행",
                           src["slug"], exc)
        await session.execute(_DELETE_WIKI_PAGE_SQL, {"page_id": src["id"]})

    return summary


# ─────────────────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────────────────


async def main(dry_run: bool, limit: int, min_len: int) -> None:
    Session = get_session_factory()
    print("=" * 70, flush=True)
    print(f"merge_duplicate_wikis — dry_run={dry_run} limit={limit} min_len={min_len}",
          flush=True)
    print("=" * 70, flush=True)

    # 1) discovery — duplicate title groups
    async with Session() as s:
        # placeholder 비교는 case-insensitive — wiki_pages.title 의 정규화 (LOWER+TRIM)
        # 와 매칭. 'Social Media Title Tag' vs 'social media title tag' 같은 케이스 보호.
        placeholders_lower = [p.lower() for p in _PLACEHOLDER_TITLES]
        groups_rows = (await s.execute(
            _FIND_DUPLICATE_GROUPS_SQL,
            {
                "min_len": min_len,
                "placeholders": placeholders_lower,
                "limit": limit,
            },
        )).all()

    if not groups_rows:
        print("✅ 중복 title group 없음 — 종료", flush=True)
        return

    total_extras = sum(int(r[1]) - 1 for r in groups_rows)
    print(f"발견된 중복 group: {len(groups_rows)} (정리될 wiki 수: ~{total_extras})",
          flush=True)
    print(f"  상위 5건:", flush=True)
    for r in groups_rows[:5]:
        print(f"    [{r[1]}건] {r[0][:70]}", flush=True)
    print("", flush=True)

    # 2) 각 group 처리 — 그룹마다 별 session (commit 단위)
    # tqdm 진행률 + 평균 처리 시간 + ETA (wiki_writer_batch 패턴 미러).
    bar = tqdm(
        total=len(groups_rows),
        desc=("🔗 wiki 통합 (DRY)" if dry_run else "🔗 wiki 통합"),
        unit="group",
        smoothing=0.1,         # 최근 group 위주 ETA (vLLM merge 변동 안정)
        mininterval=0.5,
        bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}{postfix}]",
    )

    stats = {
        "groups_processed": 0,
        "wikis_merged": 0,
        "items_moved": 0,
        "vllm_calls": 0,
    }
    via_counts: dict[str, int] = {}
    start_ts = time.monotonic()

    try:
        for r in groups_rows:
            norm_title = r[0]
            async with Session() as session:
                try:
                    # 그룹 안 wiki 들 fetch + merge — 단일 transaction.
                    # SQLAlchemy 2.0 async session 은 첫 execute 시 transaction 자동 시작 (autobegin),
                    # session 종료 시 commit (변경 X 면 noop).
                    wikis = [
                        dict(row) for row in (
                            await session.execute(
                                _FETCH_GROUP_WIKIS_SQL,
                                {"norm_title": norm_title},
                            )
                        ).mappings().all()
                    ]
                    if len(wikis) < 2:
                        # race — 다른 process 가 먼저 정리. skip.
                        await session.rollback()
                        bar.update(1)
                        continue

                    summary = await merge_group(
                        session,
                        norm_title=norm_title,
                        wikis=wikis,
                        dry_run=dry_run,
                    )
                    if dry_run:
                        await session.rollback()
                    else:
                        await session.commit()

                    stats["groups_processed"] += 1
                    stats["wikis_merged"] += len(summary["merged_slugs"])
                    stats["items_moved"] += summary["items_moved"]
                    via = summary.get("body_merged_via") or "?"
                    via_counts[via] = via_counts.get(via, 0) + 1
                    if via == "vllm_merged":
                        stats["vllm_calls"] += 1

                    # tqdm postfix 갱신 — 현재까지 통계 + via breakdown 일부
                    bar.set_postfix(
                        merged=stats["wikis_merged"],
                        items=stats["items_moved"],
                        vllm=stats["vllm_calls"],
                        refresh=False,
                    )

                except Exception as exc:  # noqa: BLE001
                    await session.rollback()
                    tqdm.write(
                        f"⚠ group 처리 실패 (norm_title={norm_title[:50]}): {exc}"
                    )
                finally:
                    bar.update(1)
    finally:
        bar.close()
        elapsed = time.monotonic() - start_ts

        print("\n" + "=" * 70, flush=True)
        if dry_run:
            print(f"✅ DRY RUN 완료 — {len(groups_rows)} group 진단 ", flush=True)
            print(f"   예상 정리: ~{total_extras} wiki 삭제 + 1 대표 통합", flush=True)
        else:
            print(f"✅ 완료 — groups={stats['groups_processed']}, "
                  f"wikis_merged={stats['wikis_merged']}, "
                  f"items_moved={stats['items_moved']}, "
                  f"vllm_calls={stats['vllm_calls']}",
                  flush=True)
        print(f"   소요 시간: {elapsed:.0f}s "
              f"({elapsed/60:.1f}분, {elapsed/3600:.2f}시간)", flush=True)
        if stats["groups_processed"] > 0:
            print(f"   group 당 평균: {elapsed / stats['groups_processed']:.2f}s",
                  flush=True)
        if via_counts:
            print(f"   body merge breakdown:", flush=True)
            for k, v in sorted(via_counts.items(), key=lambda x: -x[1]):
                print(f"      {k}: {v}", flush=True)


async def _entry(dry_run: bool, limit: int, min_len: int) -> None:
    """asyncio.run 진입점 — main + close_engine 모두 같은 event loop 안."""
    try:
        await main(dry_run, limit, min_len)
    finally:
        await close_engine()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="같은 source title 의 wiki 들을 1개로 merge",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="실제 변경 X, 통계만 출력")
    parser.add_argument("--limit", type=int, default=10000,
                        help="처리할 group 수 (default 10000 = 전체)")
    parser.add_argument("--min-len", type=int, default=_MIN_TITLE_LEN,
                        help=f"normalized title 최소 길이 (default {_MIN_TITLE_LEN})")
    args = parser.parse_args()

    asyncio.run(_entry(args.dry_run, args.limit, args.min_len))
    sys.stdout.flush()
