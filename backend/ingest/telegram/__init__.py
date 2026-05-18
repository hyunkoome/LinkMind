"""
Telegram ingest — 두 진입점:

1. **단일 메시지** (`ingest_telegram_message`): Telethon watcher / OpenClaw 가 메시지
   하나씩 받아 호출. URL 이 있으면 그 URL 의 host 별 ingester (`ingest_auto` 흐름)
   로 자동 라우팅, 텍스트만 있으면 source_type='telegram' 으로 raw 저장.

2. **Export 폴더** (`ingest_telegram_export`): Telegram Desktop 의
   "Export chat history" → result.json 디렉토리 파싱. Slack export 패턴과 일관.

CLAUDE.md §3 NEVER 목록의 "Telegram/Slack 봇을 LinkMind 안에 직접 만들기" 는
이 모듈 외부 (scripts/telegram_inbox_watcher.py 같은 daemon, OpenClaw 등) 에서
처리. backend.ingest.telegram 자체는 그저 파서 + ingest 진입점 — Slack 의
ingest/slack 과 동일한 위치.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from backend.db.connection import get_engine
from backend.db.repository import find_item_by_hash, insert_item
from backend.ingest.url import (
    ExtractedDoc,
    _embed_and_index,
    _generate_and_save_summary,
    auto_link_topics,
)
from backend.utils.external_ids import extract_external_ids
from backend.utils.hashing import sha256_text

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────


_URL_RE = re.compile(
    r"https?://[^\s<>\"'\)\]]+",
    re.IGNORECASE,
)


@dataclass
class TelegramAttachment:
    """텔레그램 메시지에 함께 온 파일 첨부 (Phase 2.5 wave-3).

    watcher 가 Telethon download_media 로 임시 경로에 받은 후 채워서 ingest 로 전달.
    임시 파일은 ingest 완료 후 watcher 가 정리 (storage 에 이미 sha256 dedup 으로 복사됨).
    """

    file_path: str          # 다운로드된 로컬 경로 (임시 디렉토리)
    file_name: str          # 원 파일명 (예: "report.pdf"). None 이면 watcher 가 mime 으로 생성.
    mime_type: str | None   # Telethon msg.file.mime_type
    size: int               # bytes


@dataclass
class TelegramMessage:
    """파서 / watcher 가 채우는 표준 메시지 dataclass."""

    msg_id: int | str
    date: datetime | None              # 메시지 시각 (UTC 가 가능하면 그것으로)
    text: str                          # 본문 (포맷 entity 제거된 평문). attachments 가 있으면 캡션 역할.
    sender: str | None = None          # 'from' 표시명
    sender_id: str | None = None       # 'from_id' (Telegram user/peer id)
    channel: str | None = None         # 채널 이름 (export 파일의 'name')
    channel_id: str | None = None      # 채널 id (peer id)
    permalink: str | None = None       # 'https://t.me/c/<id>/<msg>' 형태 (private 도)
    attachments: list[TelegramAttachment] = field(default_factory=list)   # Phase 2.5 wave-3


def _telegram_permalink(channel_id: str | None, msg_id: int | str) -> str | None:
    """채널의 peer id 와 msg id 로 t.me 영구 링크 생성. private channel 도 가능.

    Telegram Desktop export 의 id 는 절대값. private channel 의 t.me/c/<id>/<msg>
    에는 channel 의 raw id (음수 부호 + 100 prefix 제거) 가 들어가지만, 우리는
    export 의 id 를 그대로 사용 — 정확한 변환은 channel-specific.
    """
    if not channel_id or not msg_id:
        return None
    cid = str(channel_id).lstrip("-").removeprefix("100")
    return f"https://t.me/c/{cid}/{msg_id}"


def _extract_text(raw_text: Any) -> str:
    """Telegram export 의 'text' 필드는 string 또는 list[str | dict].

    dict 항목은 entity (mention / bold / link 등) — 그 안의 'text' 만 합침.
    """
    if not raw_text:
        return ""
    if isinstance(raw_text, str):
        return raw_text
    if isinstance(raw_text, list):
        parts: list[str] = []
        for el in raw_text:
            if isinstance(el, str):
                parts.append(el)
            elif isinstance(el, dict):
                parts.append(str(el.get("text") or ""))
        return "".join(parts)
    return str(raw_text)


def _find_urls(text: str) -> list[str]:
    """텍스트에서 http(s) URL 추출. duplicate 제거 + 순서 보존."""
    seen: set[str] = set()
    out: list[str] = []
    for m in _URL_RE.finditer(text or ""):
        u = m.group(0).rstrip(".,;:!?\")]}>")  # trailing 구두점 strip
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


# ──────────────────────────────────────────────────────────────
# 단일 메시지 ingest
# ──────────────────────────────────────────────────────────────


async def ingest_telegram_message(
    message: TelegramMessage, *, analyze_now: bool = True, force: bool = False,
) -> dict[str, Any]:
    """단일 텔레그램 메시지 처리.

    동작 (Phase 2.5 wave-3 확장):
    - URL 이 1개 이상 있으면 → 각 URL 에 대해 `/ingest/auto` 흐름 (host 별 라우팅)
    - 첨부 파일이 1개 이상 있으면 → 각 첨부에 대해 `ingest_document` 호출
      (PDF/DOCX/PPTX/TXT/MD 텍스트 추출 + 그 외 포맷은 attachment 만 저장)
    - 메시지의 text 는 attachments 가 있으면 각 attachment item 의 user_notes 로
      자동 채움 (caption → 사용자 메모, §1 학습 데이터 비전)
    - URL 도 attachment 도 없으면 → source_type='telegram' 으로 raw text 저장

    반환:
        {urls_ingested: [...], attachments_ingested: [...], note_item_id: ...,
         caption: <text 가 attachments 와 같이 왔으면 그것>}.

    inbox 패턴: ChannelAgent.is_ingest_successful 이 모든 키 (urls + attachments +
    note) 가 error 없으면 True — 그때만 채널에서 메시지 삭제.
    """
    urls = _find_urls(message.text)
    attachments = message.attachments or []
    caption = message.text.strip() if attachments and message.text else None

    result: dict[str, Any] = {
        "msg_id": str(message.msg_id),
        "urls_ingested": [],
        "attachments_ingested": [],
        "note_item_id": None,
        "caption": caption,
    }

    if urls:
        # lazy import — 순환 의존 피하기.
        from backend.api.ingest import _classify_url
        from backend.ingest.github import ingest_github
        from backend.ingest.pdf import ingest_pdf
        from backend.ingest.url import ingest_url
        from backend.ingest.youtube import ingest_youtube

        for url in urls:
            kind = _classify_url(url)
            try:
                if kind == "youtube":
                    r = await ingest_youtube(url, analyze_now=analyze_now, force=force)
                elif kind == "github":
                    r = await ingest_github(url, analyze_now=analyze_now, force=force)
                elif kind == "pdf":
                    r = await ingest_pdf(url, analyze_now=analyze_now, force=force)
                else:
                    r = await ingest_url(url, analyze_now=analyze_now, force=force)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "telegram URL ingest 실패 (msg=%s, url=%s): %s: %s",
                    message.msg_id, url, type(e).__name__, e,
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
                        "telegram": {
                            "msg_id": str(message.msg_id),
                            "channel": message.channel,
                            "channel_id": message.channel_id,
                            "sender": message.sender,
                            "sender_id": message.sender_id,
                            "permalink": message.permalink,
                            "date": message.date.isoformat() if message.date else None,
                            "caption": caption,
                        },
                    },
                    analyze_now=analyze_now,
                    force=force,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "telegram attachment ingest 실패 (msg=%s, file=%s): %s: %s",
                    message.msg_id, att.file_name, type(e).__name__, e,
                )
                r = {"filename": att.file_name, "error": str(e)}
            r["filename"] = att.file_name
            result["attachments_ingested"].append(r)

    # URL/attachment 둘 다 없을 때만 note 저장 — attachments 가 있으면 caption 이 이미
    # user_notes 로 들어감 (note 중복 방지).
    if not urls and not attachments:
        text_only = message.text.strip()
        if text_only and len(text_only) >= 20:
            note_id = await _save_text_message(message, analyze_now=analyze_now)
            result["note_item_id"] = note_id

    return result


async def _save_text_message(
    message: TelegramMessage, *, analyze_now: bool,
) -> str | None:
    """텍스트 메시지를 source_type='telegram' item 으로 저장 (idempotent)."""
    body = message.text
    if not body or len(body) < 20:
        return None
    content_hash = sha256_text(body)
    engine = get_engine()
    sf = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with sf() as session:
        existing = await find_item_by_hash(
            session, source_type="telegram", content_hash=content_hash,
        )
        if existing is not None:
            return str(existing)

        ext_ids = extract_external_ids(text=body)
        title = body.splitlines()[0][:120] if body else None
        item_id = await insert_item(
            session,
            source_type="telegram",
            raw_content=body,
            raw_content_hash=content_hash,
            source_id=f"{message.channel_id or 'unknown'}_{message.msg_id}",
            source_url=message.permalink,
            source_metadata={
                "channel": message.channel,
                "channel_id": message.channel_id,
                "sender": message.sender,
                "sender_id": message.sender_id,
                "date": message.date.isoformat() if message.date else None,
                "external_ids": [{"kind": x.kind, "value": x.value} for x in ext_ids],
            },
            title=title,
            source_created_at=message.date,
        )
        await auto_link_topics(
            session, item_id=item_id, source_type="telegram",
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


def parse_export_messages(export_path: Path) -> Iterable[TelegramMessage]:
    """Telegram Desktop "Export chat history" 의 result.json 파싱.

    export_path 가 디렉토리면 그 안의 result.json 을 찾고, .json 파일이면 그것 그대로.
    `messages` 항목 중 `type=='message'` 인 것만 yield.
    """
    p = Path(export_path)
    if p.is_dir():
        candidate = p / "result.json"
        if not candidate.exists():
            jsons = sorted(p.glob("*.json"))
            if not jsons:
                raise ValueError(f"Telegram export JSON 을 찾지 못했습니다: {export_path}")
            candidate = jsons[0]
        p = candidate

    with p.open(encoding="utf-8") as f:
        data = json.load(f)

    channel = data.get("name") or data.get("title")
    channel_id = str(data.get("id") or "")

    for m in data.get("messages") or []:
        if m.get("type") != "message":
            continue
        text = _extract_text(m.get("text"))
        if not text:
            continue
        date: datetime | None = None
        unix = m.get("date_unixtime")
        if unix:
            try:
                date = datetime.fromtimestamp(int(unix))
            except (TypeError, ValueError):
                date = None
        if date is None and m.get("date"):
            try:
                date = datetime.fromisoformat(str(m["date"]).replace("Z", "+00:00"))
            except ValueError:
                date = None

        msg_id = m.get("id")
        yield TelegramMessage(
            msg_id=msg_id,
            date=date,
            text=text,
            sender=m.get("from"),
            sender_id=str(m.get("from_id") or "") or None,
            channel=channel,
            channel_id=channel_id or None,
            permalink=_telegram_permalink(channel_id, msg_id),
        )


async def ingest_telegram_export(
    export_path: Path, *, analyze_now: bool = True, force: bool = False,
) -> dict[str, Any]:
    """Telegram export 폴더 / JSON 을 통째로 ingest. 메시지마다 ingest_telegram_message."""
    counts = {"processed": 0, "urls": 0, "notes": 0, "errors": 0}
    for msg in parse_export_messages(Path(export_path)):
        try:
            r = await ingest_telegram_message(msg, analyze_now=analyze_now, force=force)
            counts["urls"] += len(r["urls_ingested"])
            if r.get("note_item_id"):
                counts["notes"] += 1
            counts["processed"] += 1
        except Exception as e:  # noqa: BLE001
            logger.warning("telegram msg %s 실패: %s: %s", msg.msg_id, type(e).__name__, e)
            counts["errors"] += 1
    return counts
