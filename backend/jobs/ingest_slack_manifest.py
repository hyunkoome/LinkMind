"""
backend/jobs/ingest_slack_manifest.py
----------------------------------------------------------------------------
Slack export ingest 의 issues manifest 재처리.

목적 (D12 사용자 요구 — 2026-05-25): **진짜 데이터 사라진 것 아니면 모두
DB 에 입력**. raw-first §2 의 확장 — 본문 fetch 실패해도 URL + Slack
permalink + caption 은 무조건 보존.

manifest.json 의 entry 별 처리:

1. **placeholder** (633건): 이미 DB 에 들어가 있음 (검증됨 99.8%).
   source_metadata['slack'] 가 누락된 옛 row 에 보강만.

2. **exception** (320건): 99.4% 가 DB 에 못 들어감.
   - 우선 wave-5/D13 fix 가 적용된 현재 코드로 정상 ingest 재시도
     (YouTube channel handle / protocol missing / CUDA OOM 등은 재시도하면 살림)
   - 재시도도 실패하면 _save_url_only 흐름으로 URL + caption 만 보존
     (URL 자체는 존재하므로 Slack permalink + 사용자 메모 단서 영구 보존)
   - 모든 결과 item 에 source_metadata['slack'] merge

사용:
    python -m backend.jobs.ingest_slack_manifest <manifest.json>
        [--dry-run] [--limit N] [--only exception|placeholder]
        [--no-retry]

옵션:
    --dry-run    : 분류만 출력, ingest/DB 변경 없음
    --limit N    : 처음 N 건만 처리 (디버깅용)
    --only ...   : exception 또는 placeholder 만
    --no-retry   : exception 도 ingest 재시도 없이 곧장 placeholder 로 저장
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend import runtime_settings
from backend.db.connection import get_engine
from backend.db.repository import merge_source_metadata
from backend.ingest.slack import _slack_metadata
from backend.ingest.slack.export_parser import SlackMessage

logger = logging.getLogger("linkmind.ingest_slack_manifest")


def _ts_to_datetime(ts: str) -> datetime | None:
    """Slack ts ('1720622606.581589') → datetime."""
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc)
    except (ValueError, TypeError):
        return None


def _normalize_url(url: str) -> str | None:
    """URL 보강 — 'missing http://' exception 같은 단순 케이스 정규화.

    None 반환은 명백히 못 쓰는 케이스 (빈 문자열 등).
    """
    if not url or not url.strip():
        return None
    u = url.strip()
    if not u.startswith(("http://", "https://")):
        # 'www.example.com/path' → 'https://www.example.com/path'
        if u.startswith("//"):
            return "https:" + u
        if "." in u.split("/")[0]:  # 첫 토큰이 host 형태면 https:// prefix
            return "https://" + u
        return None
    return u


def _entry_to_message(entry: dict[str, Any]) -> SlackMessage:
    """manifest entry → SlackMessage (재 ingest 용 - 메타 필드는 manifest 정보만).

    manifest 가 가벼워서 user/channel_id/thread_ts/parent_text 는 알 수 없음.
    permalink/ts/channel 만 보존됨. 차후 full export 와 join 하면 풍부해짐.
    """
    ts = entry.get("ts") or ""
    return SlackMessage(
        ts=ts,
        date=_ts_to_datetime(ts),
        channel=entry.get("channel") or "",
        channel_id=None,
        text="",
        raw_text="",
        user=None,
        user_id=None,
        thread_ts=None,
        is_thread_parent=False,
        parent_text=None,
        subtype=None,
        urls=[entry.get("url")] if entry.get("url") else [],
        attachments=[],
        permalink=entry.get("permalink"),
        workspace_url=None,
    )


async def _find_existing_item_id(
    session: AsyncSession, *, source_url: str,
) -> UUID | None:
    """source_url 매칭으로 기존 item 찾기 (D12 backfill)."""
    res = await session.execute(
        sql_text(
            "SELECT id FROM items WHERE source_url = :u LIMIT 1"
        ),
        {"u": source_url},
    )
    row = res.first()
    return row[0] if row else None


async def _retry_ingest(
    url: str, kind: str, *, caption: str | None = None,
) -> dict[str, Any]:
    """현재 코드 (wave-5 fix 포함) 로 ingest 재시도. 실패 시 r['error'] 채움."""
    from backend.ingest.github import ingest_github
    from backend.ingest.pdf import ingest_pdf
    from backend.ingest.url import ingest_url
    from backend.ingest.youtube import ingest_youtube

    try:
        if kind == "youtube":
            return await ingest_youtube(url, analyze_now=True, caption=caption)
        if kind == "github":
            return await ingest_github(url, analyze_now=True, caption=caption)
        if kind == "pdf":
            return await ingest_pdf(url, analyze_now=True, caption=caption)
        return await ingest_url(url, analyze_now=True, caption=caption)
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


async def _save_placeholder(url: str, *, error: str, caption: str | None) -> dict[str, Any]:
    """ingest 가 모두 실패한 URL 을 _save_url_only 흐름으로 보존."""
    from backend.ingest.url import _save_url_only
    return await _save_url_only(url, error=error, caption=caption)


async def process_entry(
    entry: dict[str, Any], *,
    retry_exceptions: bool,
    dry_run: bool,
) -> dict[str, Any]:
    """manifest entry 한 건 처리.

    Returns 한 건의 결과 record:
      {ts, channel, manifest_url, normalized_url, kind, issue, action, item_id, note}

    action ∈ {merged_only, retried_success, retried_placeholder, saved_placeholder,
              skip, error}.

    추가로 ingest 가 만든 item 의 source_metadata 에 'manifest_input_url' (원본
    manifest URL) + 'slack' 보존. ingest 가 canonical URL 로 변환하거나 hash
    dedup 으로 기존 item 반환해도 manifest entry ↔ DB item 추적 가능.
    """
    issue = entry.get("issue")
    kind = entry.get("kind") or "url"
    raw_url = entry.get("url") or ""
    url = _normalize_url(raw_url)
    out: dict[str, Any] = {
        "ts": entry.get("ts"),
        "channel": entry.get("channel"),
        "manifest_url": raw_url,
        "normalized_url": url,
        "kind": kind,
        "issue": issue,
        "action": "skip",
        "item_id": None,
    }
    if not url:
        out["note"] = "URL 정규화 불가"
        return out

    engine = get_engine()
    sf = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    message = _entry_to_message(entry)
    slack_extra: dict[str, Any] = {
        "slack": _slack_metadata(message),
        "manifest_input_url": raw_url,
    }

    async with sf() as session:
        existing = await _find_existing_item_id(session, source_url=url)

    if existing is not None:
        # 이미 있는 item — slack metadata 만 보강 (idempotent + 안전)
        out["item_id"] = str(existing)
        out["action"] = "merged_only"
        if dry_run:
            return out
        async with sf() as session:
            changed = await merge_source_metadata(
                session, item_id=existing, extra=slack_extra,
            )
            if changed:
                await session.commit()
        return out

    # DB 에 없음 — issue 별 분기
    if issue == "placeholder" or not retry_exceptions:
        # placeholder 인데 DB 에 없는 케이스 (633 중 1건) 또는 --no-retry —
        # 곧장 _save_url_only 흐름
        if dry_run:
            out["action"] = "saved_placeholder"
            return out
        r = await _save_placeholder(
            url, error=entry.get("error") or "manifest backfill (no body)",
            caption=None,
        )
        iid = r.get("item_id")
        if iid:
            async with sf() as session:
                await merge_source_metadata(
                    session, item_id=UUID(iid), extra=slack_extra,
                )
                await session.commit()
            out["item_id"] = iid
            out["action"] = "saved_placeholder"
        else:
            out["action"] = "error"
            out["note"] = "placeholder 저장 실패"
        return out

    # exception — 재시도
    if dry_run:
        out["action"] = "retried_success_or_placeholder"
        return out
    r = await _retry_ingest(url, kind)
    if r.get("error"):
        # 재시도도 실패 → placeholder 보존
        r = await _save_placeholder(
            url, error=r["error"], caption=None,
        )
        iid = r.get("item_id")
        out["action"] = "retried_placeholder"
    else:
        iid = r.get("item_id")
        # 정상 ingest 성공이지만 chunks 0 이면 그것도 placeholder (현재 코드의 _save_url_only)
        if iid and r.get("created") and (r.get("chunks_indexed") or 0) == 0:
            out["action"] = "retried_placeholder"
        else:
            out["action"] = "retried_success"

    if iid:
        async with sf() as session:
            await merge_source_metadata(
                session, item_id=UUID(iid), extra=slack_extra,
            )
            await session.commit()
        out["item_id"] = iid
    else:
        out["action"] = "error"
    return out


_UNRESOLVED_ACTIONS: frozenset[str] = frozenset({
    "skip", "error", "saved_placeholder", "retried_placeholder",
})
"""'안 들어간' 또는 '못 살린 자료' 분류 — 사용자 후속 수동 처리 대상.

merged_only / retried_success 는 DB 에 정상적으로 들어간 (또는 이미 있던)
자료라 unresolved 가 아님. 나머지는 user 가 cleanup UI 에서 수동 보강 또는
영구 영구 미정리 처리.
"""


async def run(
    manifest_path: Path, *,
    dry_run: bool, limit: int | None,
    only: str | None, retry_exceptions: bool,
    output_dir: Path | None = None,
) -> dict[str, int]:
    """manifest 전체 처리 + 결과를 result_manifest.json + unresolved.json 으로 저장.

    output_dir 가 None 이면 입력 manifest 와 같은 디렉토리에 저장.
    """
    entries = json.loads(manifest_path.read_text())
    if only:
        entries = [e for e in entries if e.get("issue") == only]
    if limit:
        entries = entries[:limit]

    # backfill 이 별 프로세스라 runtime_settings 캐시 미적재 — 적재 (요약 prompt
    # 안전하게 가져오기). DB 죽었으면 throw — backfill 자체가 의미 없음.
    await runtime_settings.seed_and_load()

    counts: Counter[str] = Counter()
    results: list[dict[str, Any]] = []

    print(f"manifest {len(entries)} 건 처리 시작 (dry_run={dry_run}, "
          f"only={only}, retry={retry_exceptions})...")

    for i, entry in enumerate(entries, start=1):
        try:
            r = await process_entry(
                entry, retry_exceptions=retry_exceptions, dry_run=dry_run,
            )
            counts[r["action"]] += 1
            results.append(r)
            if i % 20 == 0 or i == len(entries):
                print(f"  [{i}/{len(entries)}]  "
                      + " ".join(f"{k}={v}" for k, v in sorted(counts.items())))
        except Exception as e:  # noqa: BLE001
            counts["error"] += 1
            results.append({
                "ts": entry.get("ts"),
                "channel": entry.get("channel"),
                "manifest_url": entry.get("url"),
                "kind": entry.get("kind"),
                "issue": entry.get("issue"),
                "action": "error",
                "item_id": None,
                "note": f"{type(e).__name__}: {e}",
            })
            logger.warning("entry %d 처리 실패: %s: %s", i, type(e).__name__, e)

    if not dry_run and results:
        out_dir = output_dir or manifest_path.parent
        out_dir.mkdir(parents=True, exist_ok=True)

        result_path = out_dir / "result_manifest.json"
        unresolved = [r for r in results if r["action"] in _UNRESOLVED_ACTIONS]
        unresolved_path = out_dir / "unresolved_manifest.json"

        # 가능한 idempotent — 재실행 시 덮어쓰기 (기존 결과 보존 필요하면 .bak).
        if result_path.exists():
            result_path.rename(result_path.with_suffix(
                f".bak.{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}.json"
            ))
        if unresolved_path.exists():
            unresolved_path.rename(unresolved_path.with_suffix(
                f".bak.{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}.json"
            ))

        result_path.write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        unresolved_path.write_text(
            json.dumps(unresolved, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        print(f"\n결과 저장:")
        print(f"  - 전체 처리: {result_path} ({len(results)} 건)")
        print(f"  - 미해결만:  {unresolved_path} ({len(unresolved)} 건)")

    return dict(counts)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "manifest", type=Path, nargs="?",
        default=Path(
            "archive/slack_export/issues/20260519-220427/manifest.json"
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--only", choices=["exception", "placeholder"], default=None,
    )
    parser.add_argument("--no-retry", action="store_true")
    parser.add_argument(
        "--output-dir", type=Path, default=None,
        help="result_manifest.json / unresolved_manifest.json 저장 위치 "
             "(기본: 입력 manifest 와 같은 디렉토리)",
    )
    args = parser.parse_args(sys.argv[1:])

    if not args.manifest.exists():
        print(f"manifest 파일 없음: {args.manifest}")
        return 1

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    counts = await run(
        args.manifest, dry_run=args.dry_run, limit=args.limit,
        only=args.only, retry_exceptions=not args.no_retry,
        output_dir=args.output_dir,
    )

    print("\n=== 최종 ===")
    for k, v in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {k:32s} {v:6d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
