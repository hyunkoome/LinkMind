"""
auto_link_categories — items.tags 의 빈도 분석 → categories 자동 시드 + topic_categories 자동 link.

Phase 2.5 wave-3 의 keyword 카테고리 노드 계층 부트스트랩.

흐름:
  1. items.tags 의 전체 tag 빈도 집계 (이미 LLM 이 한국어/영어 섞어 발행한 raw 해시태그).
  2. tag 의 slug 정규화 — lowercase + 알파/숫자/한글 외 → '-' + dedup.
  3. 빈도 ≥ MIN_FREQ 인 tag 를 카테고리로 시드 (label = 원본 표기, slug = 정규화).
       - 이미 같은 slug 가 있으면 synonyms 에 원본 표기를 union.
  4. 각 topic 에 대해 그 topic 의 모든 item tags 의 union → 거기 매칭되는 category 들에
     topic_categories(source='auto') link.

`--dry-run` 으로 변경 없이 결과만 출력.
`--min-freq N` 으로 카테고리 시드 임계값 조정 (default 3).

```
python -m backend.jobs.auto_link_categories --dry-run
python -m backend.jobs.auto_link_categories                # 실제 적용
python -m backend.jobs.auto_link_categories --min-freq 5
```
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
from collections import Counter

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.connection import get_engine
from backend.db.repository import (
    link_topic_to_category,
    upsert_category,
)


logger = logging.getLogger("linkmind.auto_link_categories")


# ──────────────────────────────────────────────────────────────
# slug 정규화
# ──────────────────────────────────────────────────────────────

# 한국어/영어/숫자 외는 모두 '-' 로. 연속 '-' 는 하나로.
_SLUG_RE = re.compile(r"[^A-Za-z0-9가-힣]+")
_SLUG_TRIM = re.compile(r"^-+|-+$")


def normalize_tag_slug(tag: str) -> str:
    """LLM 해시태그를 카테고리 slug 로 정규화.

    - lowercase (영문만 — 한글은 그대로)
    - 알파/숫자/한글 외 → '-'
    - 양끝/연속 '-' 정리
    - 빈/너무 짧음 (<2자) → '' 반환 (caller 가 거름)
    """
    if not tag:
        return ""
    s = tag.strip().lower()
    s = _SLUG_RE.sub("-", s)
    s = _SLUG_TRIM.sub("", s)
    if len(s) < 2:
        return ""
    return s


# 너무 일반적이라 카테고리로 가치 없는 tag — 시드에서 제외.
_TAG_STOPWORDS = {
    "ai", "ml", "dl", "tech", "research", "paper", "code", "github",
    "youtube", "video", "blog", "news", "tutorial", "review",
    "no-transcript", "no-license", "url-only", "fetch-failed",
    "has-paper-link", "has-author-meta", "pdf", "arxiv-seeded",
    "기타", "참고",
}


# ──────────────────────────────────────────────────────────────
# Job 본체
# ──────────────────────────────────────────────────────────────


async def run_job(*, min_freq: int, dry_run: bool) -> dict[str, int]:
    """전체 흐름. 반환: {categories_created, links_created, tags_skipped}.

    dry_run 이면 DB 변경 없이 결과만 계산.
    """
    engine = get_engine()
    sf = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    stats = {"categories_seeded": 0, "links_created": 0, "tags_below_freq": 0}

    async with sf() as session:
        # 1. 전체 items 의 tags 집계 (slug 단위 빈도, slug → 원본 표기 union)
        rows = (
            await session.execute(
                text("SELECT tags FROM items WHERE tags IS NOT NULL AND array_length(tags,1) > 0")
            )
        ).all()

        slug_freq: Counter[str] = Counter()
        slug_to_labels: dict[str, set[str]] = {}  # 원본 표기들 (synonyms 후보)
        for (tags,) in rows:
            seen_slugs_in_item: set[str] = set()  # 같은 item 안 중복 tag 는 1로
            for tag in tags or []:
                slug = normalize_tag_slug(tag)
                if not slug or slug in _TAG_STOPWORDS:
                    continue
                if slug in seen_slugs_in_item:
                    continue
                seen_slugs_in_item.add(slug)
                slug_freq[slug] += 1
                slug_to_labels.setdefault(slug, set()).add(tag.strip())

        # 2. 빈도 ≥ min_freq 인 것만 카테고리 시드 후보
        seed_candidates = [
            (slug, freq) for slug, freq in slug_freq.most_common()
            if freq >= min_freq
        ]
        stats["tags_below_freq"] = len(slug_freq) - len(seed_candidates)

        logger.info(
            "총 unique slug=%d, min_freq=%d 통과=%d (skip=%d)",
            len(slug_freq), min_freq, len(seed_candidates), stats["tags_below_freq"],
        )

        # 3. 카테고리 시드 — items.tags 빈도 ≥ min_freq 인 모든 slug 시드.
        # 빈 카테고리 우려는 4단계 의 fallback topic 로직 + ingest 시점 fallback 으로
        # 해결 (사용자 데이터 보존 우선, 카테고리는 사용자 tag 의 1차 시민).
        seed_slugs: set[str] = {s for s, _ in seed_candidates}
        slug_to_id: dict[str, str] = {}
        for slug, freq in seed_candidates:
            labels = sorted(slug_to_labels.get(slug, {slug}))
            label = labels[0] if labels else slug
            synonyms = labels[1:] if len(labels) > 1 else []
            if dry_run:
                logger.info(
                    "  [dry] seed category slug=%s label=%s freq=%d synonyms=%s",
                    slug, label, freq, synonyms,
                )
                continue
            cid = await upsert_category(
                session, slug=slug, label=label,
                description=f"자동 시드: {freq} item 에서 등장",
                synonyms=synonyms, color=None, pinned=False,
            )
            slug_to_id[slug] = str(cid)
            stats["categories_seeded"] += 1
        if not dry_run:
            await session.commit()

        # 4. topic ↔ category link. topic 의 tags = (topic.tags ∪ items.tags of topic).
        # fallback topic 흐름 덕분에 external_id 없는 url 도 자체 topic 을 가지므로
        # 그 topic 의 tag 가 곧 매칭 키 — Houdini 같은 카테고리도 link 정상.
        topic_rows = (
            await session.execute(
                text("""
                    SELECT t.id AS topic_id, t.tags AS topic_tags,
                           array_agg(DISTINCT unnested.tag) AS item_tags
                      FROM topics t
                      LEFT JOIN item_topics it ON it.topic_id = t.id
                      LEFT JOIN items i ON i.id = it.item_id
                      LEFT JOIN LATERAL unnest(coalesce(i.tags, '{}'::text[])) AS unnested(tag) ON TRUE
                     GROUP BY t.id, t.tags
                """)
            )
        ).all()
        for r in topic_rows:
            topic_id = r.topic_id
            all_tags: set[str] = set(r.topic_tags or [])
            for tag in (r.item_tags or []):
                if tag:
                    all_tags.add(tag)
            matched_slugs: set[str] = set()
            for tag in all_tags:
                slug = normalize_tag_slug(tag)
                if slug and slug in seed_slugs:
                    matched_slugs.add(slug)
            for slug in matched_slugs:
                if dry_run:
                    logger.info("  [dry] link topic=%s -> category=%s", topic_id, slug)
                    stats["links_created"] += 1
                    continue
                cid = slug_to_id.get(slug)
                if not cid:
                    continue
                changed = await link_topic_to_category(
                    session, topic_id=topic_id, category_id=cid, source="auto",
                )
                if changed:
                    stats["links_created"] += 1
        if not dry_run:
            await session.commit()

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="items.tags → categories 자동 시드 + topic link")
    parser.add_argument("--min-freq", type=int, default=3, help="카테고리 시드 임계값 (default 3)")
    parser.add_argument("--dry-run", action="store_true", help="변경 없이 결과만 출력")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(name)s : %(message)s")

    stats = asyncio.run(run_job(min_freq=args.min_freq, dry_run=args.dry_run))
    print()
    print("=" * 60)
    print(f"  mode             : {'DRY RUN' if args.dry_run else 'APPLY'}")
    print(f"  categories seeded: {stats['categories_seeded']}")
    print(f"  links created    : {stats['links_created']}")
    print(f"  tags below freq  : {stats['tags_below_freq']}")
    print("=" * 60)


if __name__ == "__main__":
    main()
