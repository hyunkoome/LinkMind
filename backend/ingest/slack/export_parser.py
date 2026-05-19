"""
Slack export 파서 — slackdump standard 포맷 디렉토리 → SlackMessage iterator.

slackdump (https://github.com/rusq/slackdump) standard export 출력 구조:

    <export_dir>/
    ├── channels.json        # 모든 채널 메타 (id, name, topic 등)
    ├── users.json           # 모든 사용자 메타
    ├── dms.json, groups.json, mpims.json
    ├── attachments/         # 첨부 파일 (flat, <file_id>-<name>)
    └── <channel_name>/      # 각 채널별 디렉토리
        ├── 2024-07-10.json  # 날짜별 메시지 배열
        └── ...

채널 단위로 모든 메시지를 모은 후 (thread 부모 ↔ 자식의 caption 연결) yield —
backend.ingest.slack.ingest_slack_export 가 이를 받아 ingest_slack_message 로 라우팅.

CLAUDE.md §2 raw-first 원칙: slackdump 출력은 변형 X (참조만).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# Dataclasses
# ──────────────────────────────────────────────────────────────


@dataclass
class SlackAttachment:
    """Slack 메시지의 첨부 파일.

    slackdump 가 attachments/<file_id>-<original_name> 로 받아둠. file_path 는
    resolve 된 절대경로 — ingest 시점에 그대로 ingest_document 로 넘김.
    """

    file_path: str
    file_name: str
    file_id: str            # Slack file id (예: "F083QKVNB0F")
    mime_type: str | None
    size: int


@dataclass
class SlackMessage:
    """파서가 yield 하는 표준 메시지 dataclass."""

    ts: str                              # Slack 메시지 timestamp (id 겸함, "1720622606.581589")
    date: datetime | None                # ts → datetime
    channel: str                         # 채널 이름 (디렉토리명)
    channel_id: str | None               # C0... id (channels.json 으로 매핑)
    text: str                            # cleaned text (mrkdwn entity 정리됨)
    raw_text: str                        # 원본 mrkdwn (raw 보존)
    user: str | None = None              # display_name (user_profile 또는 users.json)
    user_id: str | None = None           # U0... id
    thread_ts: str | None = None         # 부모 ts (자기 자신이면 thread 부모)
    is_thread_parent: bool = False
    parent_text: str | None = None       # thread 부모의 cleaned text (자식만 채워짐)
    subtype: str | None = None
    urls: list[str] = field(default_factory=list)
    attachments: list[SlackAttachment] = field(default_factory=list)
    permalink: str | None = None
    workspace_url: str | None = None


# ──────────────────────────────────────────────────────────────
# Filtering — 시스템/봇 메시지 (raw 정보 가치 낮음, skip)
# ──────────────────────────────────────────────────────────────


_SKIP_SUBTYPES = {
    "channel_join", "channel_leave", "channel_topic", "channel_purpose",
    "channel_name", "channel_archive", "channel_unarchive",
    "pinned_item", "unpinned_item", "reminder_add",
    "bot_message",
}


# ──────────────────────────────────────────────────────────────
# mrkdwn entity 정리
# ──────────────────────────────────────────────────────────────


# `<url>` 또는 `<url|label>`
_SLACK_LINK_RE = re.compile(r"<(https?://[^|>\s]+)(?:\|([^>]*))?>")
# `<@U123>` user mention
_SLACK_USER_MENTION_RE = re.compile(r"<@([UW][A-Z0-9]+)(?:\|[^>]*)?>")
# `<#C123|name>` channel ref
_SLACK_CHANNEL_REF_RE = re.compile(r"<#([CG][A-Z0-9]+)(?:\|([^>]*))?>")
# `<!here>`, `<!channel>`, `<!subteam^...>`
_SLACK_SPECIAL_RE = re.compile(r"<!([^>|]+)(?:\|[^>]*)?>")
# raw URL (mrkdwn 으로 감싸지지 않은 경우, Telegram 패턴 일관)
_RAW_URL_RE = re.compile(r"https?://[^\s<>\"'\)\]]+", re.IGNORECASE)


def _decode_entities(text: str) -> str:
    """Slack mrkdwn HTML entity 디코딩 (&amp;, &lt;, &gt;)."""
    return (
        text.replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
    )


def clean_mrkdwn_text(text: str) -> str:
    """Slack mrkdwn entity → plaintext.

    - `<https://url|label>` → label (라벨 없으면 url)
    - `<@U123>` → "" (mention 제거 — caption noise)
    - `<#C123|name>` → "#name"
    - `<!here>` → "@here"
    - HTML entity 디코딩
    - 연속 공백 정규화

    raw URL 은 그대로 보존 (caption 후처리 시 _strip_urls_for_caption 가 다시 떼어냄).
    """
    if not text:
        return ""
    out = _SLACK_LINK_RE.sub(lambda m: m.group(2) or m.group(1), text)
    out = _SLACK_USER_MENTION_RE.sub("", out)
    out = _SLACK_CHANNEL_REF_RE.sub(lambda m: f"#{m.group(2) or m.group(1)}", out)
    out = _SLACK_SPECIAL_RE.sub(lambda m: f"@{m.group(1).split('^')[0]}", out)
    out = _decode_entities(out)
    out = " ".join(out.split())
    return out


def extract_urls(msg: dict[str, Any]) -> list[str]:
    """메시지의 URL 들을 blocks + mrkdwn link + raw URL 에서 모두 추출 (dedup).

    우선순위: blocks 의 link element (가장 정확) → mrkdwn `<url|label>` → raw URL.
    trailing 구두점은 strip (Telegram 패턴 일관).
    """
    seen: set[str] = set()
    out: list[str] = []

    def _add(url: str) -> None:
        if not url:
            return
        url = url.rstrip(".,;:!?\")]}>")
        if url and url not in seen:
            seen.add(url)
            out.append(url)

    # 1. blocks 의 rich_text → link element (가장 정확한 URL)
    for block in msg.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        for el in block.get("elements") or []:
            if not isinstance(el, dict):
                continue
            for inner in el.get("elements") or []:
                if isinstance(inner, dict) and inner.get("type") == "link":
                    _add(inner.get("url") or "")

    # 2. text 의 mrkdwn link `<url|label>`
    text = msg.get("text") or ""
    for m in _SLACK_LINK_RE.finditer(text):
        _add(m.group(1))

    # 3. cleaned text 의 raw URL (mrkdwn 으로 안 감싸진 경우)
    cleaned = clean_mrkdwn_text(text)
    for m in _RAW_URL_RE.finditer(cleaned):
        _add(m.group(0))

    return out


def extract_attachments(
    msg: dict[str, Any], attachments_dir: Path,
) -> list[SlackAttachment]:
    """메시지의 files 필드 → SlackAttachment 리스트.

    slackdump 가 다운로드한 파일은 attachments/<file_id>-<name>. 실제 파일이 없으면
    skip (대형 파일 다운로드 실패, 외부 파일 등).
    """
    out: list[SlackAttachment] = []
    for f in msg.get("files") or []:
        if not isinstance(f, dict):
            continue
        fid = f.get("id")
        name = f.get("name") or f.get("title") or fid
        if not fid or not name:
            continue
        path = attachments_dir / f"{fid}-{name}"
        if not path.exists():
            logger.debug("slack attachment 파일 없음 (skip): %s", path)
            continue
        out.append(SlackAttachment(
            file_path=str(path),
            file_name=str(name),
            file_id=str(fid),
            mime_type=f.get("mimetype"),
            size=int(f.get("size") or path.stat().st_size),
        ))
    return out


def _ts_to_datetime(ts: str) -> datetime | None:
    """Slack ts ("1720622606.581589") → datetime."""
    try:
        return datetime.fromtimestamp(float(ts))
    except (TypeError, ValueError):
        return None


def _slack_permalink(
    workspace_url: str | None, channel_id: str | None, ts: str,
) -> str | None:
    """Slack 영구 링크: {workspace_url}/archives/{channel_id}/p{ts.replace('.', '')}."""
    if not workspace_url or not channel_id or not ts:
        return None
    ts_compact = ts.replace(".", "")
    return f"{workspace_url.rstrip('/')}/archives/{channel_id}/p{ts_compact}"


def _user_display(
    msg: dict[str, Any], users_meta: dict[str, str],
) -> tuple[str | None, str | None]:
    """메시지에서 user_id 와 display name 추출.

    우선순위: 메시지의 user_profile 인라인 → users.json 매핑.
    """
    uid = msg.get("user") or None
    profile = msg.get("user_profile") or {}
    display = (
        profile.get("display_name")
        or profile.get("real_name")
        or profile.get("name")
    )
    if not display and uid:
        display = users_meta.get(uid)
    return uid, (display or None)


# ──────────────────────────────────────────────────────────────
# Meta 로더
# ──────────────────────────────────────────────────────────────


def load_channels_meta(export_dir: Path) -> dict[str, dict[str, Any]]:
    """channels.json → name → meta 매핑.

    name_normalized 또는 name 으로 디렉토리명 매칭. 없으면 빈 dict.
    """
    path = export_dir / "channels.json"
    if not path.exists():
        return {}
    try:
        data = json.load(path.open(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("channels.json 로드 실패: %s", e)
        return {}
    out: dict[str, dict[str, Any]] = {}
    for ch in data or []:
        if not isinstance(ch, dict):
            continue
        for key in ("name_normalized", "name"):
            name = ch.get(key)
            if name:
                out[str(name)] = ch
    return out


def load_users_meta(export_dir: Path) -> dict[str, str]:
    """users.json → user_id → display 매핑."""
    path = export_dir / "users.json"
    if not path.exists():
        return {}
    try:
        data = json.load(path.open(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("users.json 로드 실패: %s", e)
        return {}
    out: dict[str, str] = {}
    for u in data or []:
        if not isinstance(u, dict):
            continue
        uid = u.get("id")
        profile = u.get("profile") or {}
        display = (
            profile.get("display_name")
            or profile.get("real_name")
            or u.get("name")
        )
        if uid and display:
            out[str(uid)] = str(display)
    return out


# ──────────────────────────────────────────────────────────────
# 채널 → 메시지 yield
# ──────────────────────────────────────────────────────────────


def _iter_channel_dirs(
    export_dir: Path, channel_filter: str | None,
) -> Iterator[Path]:
    """export_dir 의 채널 디렉토리 순회 (attachments/ 제외, *.json 있는 것만)."""
    for d in sorted(export_dir.iterdir()):
        if not d.is_dir() or d.name == "attachments":
            continue
        if channel_filter and d.name != channel_filter:
            continue
        if not any(d.glob("*.json")):
            continue
        yield d


def _load_channel_messages(channel_dir: Path) -> list[dict[str, Any]]:
    """채널의 모든 *.json 로드 → ts 기준 정렬 후 합치기.

    날짜별 JSON 이 thread 경계를 가로지를 수 있어 전체를 한 번에 로드.
    평균 ~78 메시지/채널 — 메모리 부담 없음.
    """
    out: list[dict[str, Any]] = []
    for jp in sorted(channel_dir.glob("*.json")):
        try:
            data = json.load(jp.open(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("slack JSON 로드 실패 (%s): %s", jp, e)
            continue
        if not isinstance(data, list):
            logger.debug("slack JSON 이 array 아님 (skip): %s", jp)
            continue
        out.extend(m for m in data if isinstance(m, dict))
    # ts 기준 정렬 — thread parent → child 순서 보장
    out.sort(key=lambda m: float(m.get("ts") or 0))
    return out


def parse_slack_export(
    export_dir: str | Path,
    *,
    channel_filter: str | None = None,
    workspace_url: str | None = None,
) -> Iterator[SlackMessage]:
    """slackdump standard export → SlackMessage iterator.

    args:
        export_dir: slackdump root (channels.json 이 있는 폴더)
        channel_filter: 한 채널만 처리 (디렉토리명). None = 전체.
        workspace_url: permalink 생성용 (예: "https://hkkim.slack.com").
                       None 이면 permalink 비움.
    """
    root = Path(export_dir)
    if not root.is_dir():
        raise ValueError(f"slack export 디렉토리가 아님: {export_dir}")

    channels_meta = load_channels_meta(root)
    users_meta = load_users_meta(root)
    attachments_dir = root / "attachments"

    for ch_dir in _iter_channel_dirs(root, channel_filter):
        ch_name = ch_dir.name
        ch_meta = channels_meta.get(ch_name) or {}
        ch_id = ch_meta.get("id")

        all_msgs = _load_channel_messages(ch_dir)
        if not all_msgs:
            continue

        # ── pass 1: thread 부모의 cleaned text 누적 ────────────
        parent_texts: dict[str, str] = {}
        for m in all_msgs:
            if m.get("subtype") in _SKIP_SUBTYPES:
                continue
            ts = m.get("ts")
            tts = m.get("thread_ts")
            if ts and tts and ts == tts:
                cleaned = clean_mrkdwn_text(m.get("text") or "")
                if cleaned:
                    parent_texts[str(ts)] = cleaned

        # ── pass 2: 메시지 yield ────────────────────────────────
        for m in all_msgs:
            sub = m.get("subtype")
            if sub in _SKIP_SUBTYPES:
                continue
            ts = m.get("ts")
            if not ts:
                continue
            raw_text = m.get("text") or ""
            cleaned = clean_mrkdwn_text(raw_text)
            urls = extract_urls(m)
            attachments = extract_attachments(m, attachments_dir)
            # 정보 0 인 메시지 skip
            if not cleaned and not urls and not attachments:
                continue

            tts = m.get("thread_ts")
            is_parent = bool(tts and ts and tts == ts)
            parent_text: str | None = None
            if tts and not is_parent:
                parent_text = parent_texts.get(str(tts))

            uid, display = _user_display(m, users_meta)

            yield SlackMessage(
                ts=str(ts),
                date=_ts_to_datetime(str(ts)),
                channel=ch_name,
                channel_id=ch_id,
                text=cleaned,
                raw_text=raw_text,
                user=display,
                user_id=uid,
                thread_ts=str(tts) if tts else None,
                is_thread_parent=is_parent,
                parent_text=parent_text,
                subtype=sub,
                urls=urls,
                attachments=attachments,
                permalink=_slack_permalink(workspace_url, ch_id, str(ts)),
                workspace_url=workspace_url,
            )
