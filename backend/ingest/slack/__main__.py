"""
CLI 진입점 — `python -m backend.ingest.slack <export_dir> [options]`

slackdump 의 standard export 디렉토리 (channels.json + 채널 디렉토리 + attachments/)
를 LinkMind 로 backfill. raw-first (§2) — 사용자가 Slack 구독 해제 전 일회성.

사용 예:
    # 단일 채널만 (dry-run / 디버깅)
    python -m backend.ingest.slack archive/slack_export/latest \\
        --channel 공부-컴퓨터비전

    # 전체 워크스페이스 + permalink workspace URL
    python -m backend.ingest.slack archive/slack_export/latest \\
        --workspace-url https://hkkim.slack.com

    # 강제 재요약 (prompt 버전 올린 후 등)
    python -m backend.ingest.slack archive/slack_export/latest --force

실시간 listen 은 Phase 3+ 의 ChannelAgent 가 담당 (Telegram inbox watcher 패턴).
이 모듈은 export 파일 backfill 전용.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
from pathlib import Path

from backend.ingest.slack import ingest_slack_export


def _default_issues_path(export_dir: Path) -> Path:
    """기본 manifest 경로 — export 의 부모 (archive/slack_export/) 아래 issues/<ts>/.

    사용자 정책 (2026-05-19): manifest 는 archive/slack_export/ 하위에 보존
    (export 와 한 곳에 모음, /tmp 같은 휘발성 위치 금지). --issues-path 로 명시
    override 가능. --no-issues 로 비활성 가능.
    """
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    return export_dir.parent / "issues" / ts / "manifest.json"


async def _run(
    path: Path, *, channel: str | None, workspace_url: str | None,
    force: bool, progress: bool, issues_path: Path | None,
) -> None:
    counts = await ingest_slack_export(
        path,
        channel_filter=channel,
        workspace_url=workspace_url,
        analyze_now=True,
        force=force,
        progress=progress,
        issues_path=issues_path,
    )
    print(
        f"완료 — channels={counts['channels']}  processed={counts['processed']}  "
        f"urls={counts['urls']}  attachments={counts['attachments']}  "
        f"notes={counts['notes']}  errors={counts['errors']}  "
        f"issues={counts.get('issues', 0)}"
    )
    if counts.get("issues_path"):
        print(f"📋 issues manifest: {counts['issues_path']}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(
        prog="python -m backend.ingest.slack",
        description="slackdump standard export 디렉토리를 LinkMind 로 backfill",
    )
    p.add_argument("path", help="slackdump export 폴더 (channels.json 이 있는 root)")
    p.add_argument(
        "--channel", default=None,
        help="단일 채널만 ingest (디렉토리명, 예: '공부-컴퓨터비전')",
    )
    p.add_argument(
        "--workspace-url", default=None, dest="workspace_url",
        help="영구 링크 base URL (예: https://hkkim.slack.com). 없으면 permalink 생략",
    )
    p.add_argument(
        "--force", action="store_true",
        help="동일 hash 의 기존 item 도 summary/tags 재계산",
    )
    p.add_argument(
        "--no-progress", action="store_false", dest="progress",
        help="tqdm 진행률 표시 비활성 (logfile redirect 시 권장)",
    )
    p.add_argument(
        "--issues-path", default=None, dest="issues_path",
        help="실패/placeholder URL 매니페스트 JSON 저장 경로. "
             "기본: <export_dir 부모>/issues/<timestamp>/manifest.json "
             "(archive/slack_export/issues/...). --no-issues 로 비활성.",
    )
    p.add_argument(
        "--no-issues", action="store_true", dest="no_issues",
        help="issues manifest 비활성 (smoke test 등). 기본은 항상 archive 하위 저장.",
    )
    p.set_defaults(progress=True)
    args = p.parse_args()

    # issues-path 결정: --no-issues 면 None / 명시 있으면 그것 / 기본 자동.
    export_path = Path(args.path)
    if args.no_issues:
        issues_path: Path | None = None
    elif args.issues_path:
        issues_path = Path(args.issues_path)
    else:
        issues_path = _default_issues_path(export_path)

    asyncio.run(_run(
        export_path,
        channel=args.channel,
        workspace_url=args.workspace_url,
        force=args.force,
        progress=args.progress,
        issues_path=issues_path,
    ))
