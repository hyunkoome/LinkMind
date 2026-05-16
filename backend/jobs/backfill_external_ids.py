"""
scripts/backfill_external_ids.py
----------------------------------------------------------------------------
기존 item 들에 external_ids 와 topic auto-link 를 소급 적용.

흐름:
1. items 를 순회 — source_url + raw_content 에서 external_ids 추출.
2. items.source_metadata['external_ids'] 에 표준 키 저장 (기존 메타 보존, 머지).
3. auto_link_topics 로 topic find_or_create + link_item_to_topic.

source_metadata['external_ids'] 가 이미 있으면 skip (이미 처리됨). `--force` 면 재계산.

사용:
    python scripts/backfill_external_ids.py            # 미처리 row 만
    python scripts/backfill_external_ids.py --force    # 모두 재계산
    python scripts/backfill_external_ids.py <item_id>  # 특정 item 1개
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any
from uuid import UUID


from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker  # noqa: E402

from backend.db.connection import get_engine  # noqa: E402
from backend.db.repository import update_item_metadata  # noqa: E402
from backend.ingest.url import auto_link_topics  # noqa: E402
from backend.utils.external_ids import ExternalId, extract_external_ids  # noqa: E402


async def _fetch_targets(
    session: AsyncSession, *, item_id: UUID | None, force: bool,
) -> list[tuple[UUID, str, str | None, str, dict[str, Any]]]:
    """(id, source_type, source_url, title, source_metadata) 목록."""
    if item_id is not None:
        rows = await session.execute(
            text("""
                SELECT id, source_type, source_url, title, raw_content, source_metadata
                FROM items WHERE id = :id
            """),
            {"id": str(item_id)},
        )
    elif force:
        rows = await session.execute(text(
            "SELECT id, source_type, source_url, title, raw_content, source_metadata "
            "FROM items ORDER BY ingested_at"
        ))
    else:
        rows = await session.execute(text(
            "SELECT id, source_type, source_url, title, raw_content, source_metadata "
            "FROM items "
            "WHERE source_metadata->'external_ids' IS NULL "
            "ORDER BY ingested_at"
        ))
    return [
        (r.id, r.source_type, r.source_url, r.title, r.raw_content, r.source_metadata or {})
        for r in rows.all()
    ]


async def main() -> int:
    args = sys.argv[1:]
    force = "--force" in args
    args = [a for a in args if a != "--force"]
    item_id: UUID | None = UUID(args[0]) if args else None

    engine = get_engine()
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with session_factory() as session:
        targets = await _fetch_targets(session, item_id=item_id, force=force)
        if not targets:
            print("대상 없음 (이미 모두 external_ids 보유, 또는 item 미존재).")
            return 0

        print(f"대상 {len(targets)} 건 — external_ids + topic auto-link")
        ok, skipped = 0, 0
        for iid, source_type, source_url, title, raw, meta in targets:
            # github / youtube / pdf 류는 ingest 시점에 self external_id (e.g. github:owner/repo)
            # 가 추가됐다. backfill 에서는 source_url + raw_content 만 갖고 정직하게 추출.
            ids = extract_external_ids(url=source_url, text=raw[:20000] if raw else None)
            # github / youtube ingester 는 자기 자체 식별자도 추가하므로 backfill 도 동일하게:
            if source_type == "github":
                # raw_content 첫 줄 'GitHub: owner/repo' 에서 owner/repo 복원
                if raw and raw.startswith("GitHub: "):
                    repo = raw.splitlines()[0][len("GitHub: "):].strip()
                    if "/" in repo and not any(
                        x.kind == "github" and x.value == repo for x in ids
                    ):
                        ids.insert(0, ExternalId(kind="github", value=repo))
            elif source_type == "youtube" and meta.get("video_id"):
                vid = meta["video_id"]
                if not any(x.kind == "yt" and x.value == vid for x in ids):
                    ids.insert(0, ExternalId(kind="yt", value=vid))
            elif source_type == "youtube_playlist" and meta.get("playlist_id"):
                plid = meta["playlist_id"]
                if not any(x.kind == "ytpl" and x.value == plid for x in ids):
                    ids.insert(0, ExternalId(kind="ytpl", value=plid))

            if not ids:
                print(f"  - {iid} ({source_type}) ... ext_ids 없음, skip")
                skipped += 1
                continue

            # source_metadata 에 external_ids 표준 키 머지 — 기존 메타 보존.
            new_meta = {**meta, "external_ids": [
                {"kind": x.kind, "value": x.value} for x in ids
            ]}
            await update_item_metadata(session, item_id=iid, source_metadata=new_meta)
            matched = await auto_link_topics(
                session, item_id=iid, source_type=source_type, title=title, ids=ids,
            )
            await session.commit()
            slugs = ", ".join(t["slug"] for t in matched)
            print(f"  - {iid} ({source_type}) ... {len(matched)} topic ({slugs})")
            ok += 1

        print(f"\n완료: 처리 {ok} / skip {skipped}")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
