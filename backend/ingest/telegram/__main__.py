"""
CLI 진입점 — `python -m backend.ingest.telegram <export_path> [--force]`

Telegram Desktop 의 "Export chat history" 결과 (result.json 폴더) 를 ingest.
실시간 채널 수신은 scripts/telegram_inbox_watcher.py 가 담당.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from backend.ingest.telegram import ingest_telegram_export


async def _run(path: Path, force: bool) -> None:
    counts = await ingest_telegram_export(path, analyze_now=True, force=force)
    print(
        f"완료 — processed={counts['processed']}  "
        f"urls={counts['urls']}  notes={counts['notes']}  "
        f"errors={counts['errors']}"
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(
        prog="python -m backend.ingest.telegram",
        description="Telegram Desktop export 폴더/JSON 을 LinkMind 로 ingest",
    )
    p.add_argument("path", help="export 폴더 또는 result.json 파일 경로")
    p.add_argument("--force", action="store_true",
                   help="동일 hash 의 기존 item 도 summary/tags 재계산")
    args = p.parse_args()

    asyncio.run(_run(Path(args.path), force=args.force))
