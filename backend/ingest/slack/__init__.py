"""
Slack ingest — Telegram 모듈 패턴 일관 (backend/ingest/telegram).

진입점:
1. `ingest_slack_message(msg)`: 단일 SlackMessage 처리.
   - URL → 자동 host 라우팅 (ingest_url/youtube/github/pdf)
   - 첨부 → ingest_document
   - caption (thread parent text 또는 stripped 본문) → user_notes
   - URL/첨부 없으면 source_type='slack' note 로 raw 보존
2. `ingest_slack_export(export_dir)`: slackdump 디렉토리 전체 순회.

Phase C wave-2 (2026-05-19~) — 일회성 backfill. raw-first (§2): 사용자가 Slack
구독 해제 전에 통째로 보존. Telegram inbox watcher 와 동일 패턴이라 향후 Slack
실시간 사용 시 ChannelAgent 로 wrap 가능.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from backend.db.connection import get_engine
from backend.db.repository import find_item_by_hash, insert_item
from backend.ingest.slack.export_parser import (
    SlackAttachment,
    SlackMessage,
    clean_mrkdwn_text,
    extract_attachments,
    extract_urls,
    parse_slack_export,
)
from backend.ingest.telegram import _strip_urls_for_caption  # 동일 caption 규칙 재사용
from backend.ingest.url import (
    ExtractedDoc,
    _embed_and_index,
    _generate_and_save_summary,
    auto_link_topics,
)
from backend.utils.external_ids import extract_external_ids
from backend.utils.hashing import sha256_text

logger = logging.getLogger(__name__)

# 텍스트 메모 (URL/첨부 없는 메시지) 최소 길이 — Telegram 과 일관
_NOTE_MIN_LEN = 20

__all__ = [
    "SlackAttachment",
    "SlackMessage",
    "clean_mrkdwn_text",
    "extract_attachments",
    "extract_urls",
    "ingest_slack_message",
    "ingest_slack_export",
    "parse_slack_export",
]


# ──────────────────────────────────────────────────────────────
# 단일 메시지 ingest
# ──────────────────────────────────────────────────────────────


def _resolve_caption(message: SlackMessage) -> str | None:
    """caption 결정 — thread 그룹핑 보존이 핵심.

    - thread 자식이고 parent_text 있으면 → 그것 (논문 제목 같은 묶음 라벨)
    - 첨부 있으면 → text 그대로 (사용자 메모 원본)
    - URL 있으면 → text 에서 URL 제거한 나머지 (Telegram 패턴)
    - 그 외 → None
    """
    if message.parent_text and not message.is_thread_parent:
        return message.parent_text
    if message.attachments and message.text:
        return message.text.strip() or None
    if message.urls and message.text:
        return _strip_urls_for_caption(message.text) or None
    return None


def _slack_metadata(message: SlackMessage, *, file_id: str | None = None) -> dict[str, Any]:
    """source_metadata['slack'] 공통 dict."""
    out: dict[str, Any] = {
        "ts": message.ts,
        "channel": message.channel,
        "channel_id": message.channel_id,
        "user": message.user,
        "user_id": message.user_id,
        "thread_ts": message.thread_ts,
        "is_thread_parent": message.is_thread_parent,
        "permalink": message.permalink,
        "date": message.date.isoformat() if message.date else None,
    }
    if file_id:
        out["file_id"] = file_id
    return out


async def ingest_slack_message(
    message: SlackMessage, *, analyze_now: bool = True, force: bool = False,
) -> dict[str, Any]:
    """단일 Slack 메시지 처리 (Telegram 패턴 일관).

    반환: {ts, channel, urls_ingested, attachments_ingested, note_item_id, caption}.
    """
    urls = message.urls
    attachments = message.attachments
    caption = _resolve_caption(message)

    result: dict[str, Any] = {
        "ts": message.ts,
        "channel": message.channel,
        "urls_ingested": [],
        "attachments_ingested": [],
        "note_item_id": None,
        "caption": caption,
    }

    if urls:
        # lazy import — 순환 의존 피하기 (Telegram 동일 패턴)
        from backend.api.ingest import _classify_url
        from backend.ingest.github import ingest_github
        from backend.ingest.pdf import ingest_pdf
        from backend.ingest.url import ingest_url
        from backend.ingest.youtube import ingest_youtube

        for url in urls:
            kind = _classify_url(url)
            try:
                if kind == "youtube":
                    r = await ingest_youtube(
                        url, analyze_now=analyze_now, force=force, caption=caption,
                    )
                elif kind == "github":
                    r = await ingest_github(
                        url, analyze_now=analyze_now, force=force, caption=caption,
                    )
                elif kind == "pdf":
                    r = await ingest_pdf(
                        url, analyze_now=analyze_now, force=force, caption=caption,
                    )
                else:
                    r = await ingest_url(
                        url, analyze_now=analyze_now, force=force, caption=caption,
                    )
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "slack URL ingest 실패 (ch=%s, ts=%s, url=%s): %s: %s",
                    message.channel, message.ts, url, type(e).__name__, e,
                )
                r = {"url": url, "error": str(e)}
            r["url"] = url
            r["kind"] = kind
            result["urls_ingested"].append(r)

    if attachments:
        from backend.ingest.document import ingest_document

        for att in attachments:
            try:
                r = await ingest_document(
                    att.file_path,
                    filename=att.file_name,
                    caption=caption,
                    source_metadata_extra={
                        "mime_type": att.mime_type,
                        "slack": _slack_metadata(message, file_id=att.file_id),
                    },
                    analyze_now=analyze_now,
                    force=force,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "slack attachment ingest 실패 (ch=%s, ts=%s, file=%s): %s: %s",
                    message.channel, message.ts, att.file_name, type(e).__name__, e,
                )
                r = {"filename": att.file_name, "error": str(e)}
            r["filename"] = att.file_name
            result["attachments_ingested"].append(r)

    # URL/첨부 둘 다 없을 때만 note 저장 (raw 보존이 목적)
    if not urls and not attachments:
        text_only = message.text.strip()
        if text_only and len(text_only) >= _NOTE_MIN_LEN:
            note_id = await _save_text_message(message, analyze_now=analyze_now)
            result["note_item_id"] = note_id

    return result


async def _save_text_message(
    message: SlackMessage, *, analyze_now: bool,
) -> str | None:
    """텍스트 메시지를 source_type='slack' item 으로 저장 (idempotent)."""
    body = message.text
    if not body or len(body) < _NOTE_MIN_LEN:
        return None
    content_hash = sha256_text(body)
    engine = get_engine()
    sf = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with sf() as session:
        existing = await find_item_by_hash(
            session, source_type="slack", content_hash=content_hash,
        )
        if existing is not None:
            return str(existing)

        ext_ids = extract_external_ids(text=body)
        title = body.splitlines()[0][:120] if body else None
        meta = _slack_metadata(message)
        meta["external_ids"] = [{"kind": x.kind, "value": x.value} for x in ext_ids]
        item_id = await insert_item(
            session,
            source_type="slack",
            raw_content=body,
            raw_content_hash=content_hash,
            source_id=f"{message.channel_id or message.channel}_{message.ts}",
            source_url=message.permalink,
            source_metadata=meta,
            title=title,
            source_created_at=message.date,
        )
        await auto_link_topics(
            session, item_id=item_id, source_type="slack",
            title=title, ids=ext_ids,
        )
        await session.commit()

        if analyze_now:
            doc = ExtractedDoc(body=body, title=title, abstract=None, paper_keywords=[])
            await _embed_and_index(session, item_id=item_id, text=body)
            await _generate_and_save_summary(session, item_id=item_id, doc=doc)

        return str(item_id)


# ──────────────────────────────────────────────────────────────
# Export 폴더 ingest
# ──────────────────────────────────────────────────────────────


def _detect_url_issue(r: dict[str, Any]) -> str | None:
    """ingest_url/*/pdf/github/youtube 결과 dict 에서 issue 종류 판정.

    Returns 분류:
    - "exception": Python exception 으로 핸들된 (r["error"] 존재)
    - "placeholder": 새 item 으로 created 됐는데 chunks_indexed == 0
      (raw 본문 추출 실패 — readability fallback 도 못 도는 케이스. URL 은 살아있지만
      LinkedIn login wall / project page / mp4 등 본문 없는 페이지)
    - None: 정상 (성공 또는 hash dedup 으로 skip)

    refreshed=True 는 기존 item 의 force re-summary — issue 아님.
    """
    if r.get("error"):
        return "exception"
    if r.get("created") and (r.get("chunks_indexed") or 0) == 0:
        return "placeholder"
    return None


def _save_issues_manifest(path: Path, issues: list[dict[str, Any]]) -> None:
    """실패/placeholder 케이스를 JSON 파일로 저장 — 후속 별도 처리용.

    archive/slack_export/issues/<timestamp>/ 디렉토리 권장 — slack export 와
    같은 위치에 두면 추적 쉬움. 파일은 단일 manifest.json (배열) — 메시지마다
    한 줄, 분석/필터링 쉬움.
    """
    if not issues:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(issues, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


async def ingest_slack_export(
    export_dir: Path,
    *,
    channel_filter: str | None = None,
    workspace_url: str | None = None,
    analyze_now: bool = True,
    force: bool = False,
    progress: bool = False,
    issues_path: Path | None = None,
) -> dict[str, Any]:
    """slackdump export 디렉토리 통째 ingest. 메시지마다 ingest_slack_message.

    progress=True 면 tqdm 로 진행률 표시 (메시지 단위 + 현재 채널명 postfix).
    14241 메시지 전체 backfill 같은 장시간 작업에 권장. 첫 패스는 메시지 카운트
    (parse 한 번 더 도는 비용 — ~5초/만 메시지) 라 부담 작음.

    issues_path 가 주어지면 실패/placeholder 케이스를 그 경로의 JSON 으로 누적
    저장 (사용자가 "DB 에 안 들어간 자료만 따로 보존" 정책 요청). 각 entry:
      {ts, channel, permalink, url, kind, issue, error, raw_len, summary_len}
    후속 작업: 별도 로직으로 LinkedIn / project page / mp4 등 패턴별 재처리.
    """
    counts: dict[str, Any] = {
        "processed": 0, "urls": 0, "attachments": 0, "notes": 0, "errors": 0,
    }
    channels_seen: set[str] = set()
    issues: list[dict[str, Any]] = []

    if progress:
        # 첫 패스: 메시지 카운트만 (parse_slack_export 는 generator)
        total = sum(1 for _ in parse_slack_export(
            Path(export_dir),
            channel_filter=channel_filter,
            workspace_url=None,        # permalink 만들 필요 X — count 만
        ))
        from tqdm import tqdm  # lazy import (test cpu 마커 의존성 줄임)
        # mininterval=0.5 — tqdm refresh 폭주 방지 (수만 메시지 ingest 시 stderr 노이즈 ↓).
        bar = tqdm(total=total, desc="slack ingest", unit="msg", mininterval=0.5)
    else:
        bar = None

    try:
        for msg in parse_slack_export(
            Path(export_dir),
            channel_filter=channel_filter,
            workspace_url=workspace_url,
        ):
            try:
                r = await ingest_slack_message(msg, analyze_now=analyze_now, force=force)
                counts["urls"] += len(r["urls_ingested"])
                counts["attachments"] += len(r["attachments_ingested"])
                if r.get("note_item_id"):
                    counts["notes"] += 1
                counts["processed"] += 1
                channels_seen.add(msg.channel)

                # 실패/placeholder URL 만 manifest 에 누적 (issues_path 주어진 경우만 메모리에 모음).
                if issues_path is not None:
                    for url_r in r.get("urls_ingested") or []:
                        kind_issue = _detect_url_issue(url_r)
                        if kind_issue is None:
                            continue
                        issues.append({
                            "ts": msg.ts,
                            "channel": msg.channel,
                            "permalink": msg.permalink,
                            "url": url_r.get("url"),
                            "kind": url_r.get("kind"),
                            "issue": kind_issue,
                            "error": url_r.get("error"),
                            "raw_len": url_r.get("raw_len"),
                            "chunks_indexed": url_r.get("chunks_indexed") or 0,
                        })
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "slack msg %s (ch=%s) 실패: %s: %s",
                    msg.ts, msg.channel, type(e).__name__, e,
                )
                counts["errors"] += 1
                if issues_path is not None:
                    issues.append({
                        "ts": msg.ts,
                        "channel": msg.channel,
                        "permalink": msg.permalink,
                        "url": None,
                        "kind": "message",
                        "issue": "exception",
                        "error": f"{type(e).__name__}: {e}",
                    })
            if bar is not None:
                # postfix 로 현재 채널/누적 URL/error 보임 — 어느 채널에서 느려졌나/막혔나 즉시 인지.
                bar.set_postfix(
                    ch=msg.channel[:25], urls=counts["urls"],
                    errs=counts["errors"], iss=len(issues),
                    refresh=False,
                )
                bar.update(1)
    finally:
        if bar is not None:
            bar.close()

    counts["channels"] = len(channels_seen)
    counts["issues"] = len(issues)

    if issues_path is not None:
        _save_issues_manifest(issues_path, issues)
        counts["issues_path"] = str(issues_path)

    return counts
