"""
YouTube ingester — 단일 영상 또는 플레이리스트 URL 한 개를 받아 처리.

흐름
----
1. URL 종류 판별 (video / playlist)
2. yt-dlp 로 메타데이터 (title, channel, duration, description, upload_date, ...) 추출.
   playlist 면 flat list (자식 영상 메타) 까지만, 영상은 단일 info.
3. 영상의 경우 youtube-transcript-api 로 자막 시도 (ko → en 순서). 자막 있으면
   raw_content 에 description + transcript 까지 포함.
4. playlist 의 경우 raw_content 는 헤더 + 영상 목록 (요약 LLM 이 "어떤 영상들인지"
   쉽게 정리하도록 형식화) + 끝에 yt-dlp 원본 dict 의 JSON.
5. items 로 저장 후 url ingest 와 동일한 helper 로 임베딩 + 요약 + 해시태그.
   요약 입력은 transcript 가 있으면 transcript, 없으면 description 또는 영상 목록.

자막을 못 가져온 영상은 `#no-transcript` 라벨이 paper_keywords 에 추가되어 tags 로 들어감.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from urllib.parse import parse_qs, urlparse

from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from backend.db.connection import get_engine
from backend.db.repository import find_item_by_hash, insert_item
from backend.ingest.url import (
    ExtractedDoc,
    _embed_and_index,
    _generate_and_save_summary,
)
from backend.utils.hashing import sha256_text

logger = logging.getLogger(__name__)


# ── URL 파싱 ───────────────────────────────────────────────────


_YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}


def parse_youtube_url(url: str) -> dict[str, str | None]:
    """YouTube URL 을 video_id / playlist_id / kind 로 분해.

    kind: 'video' | 'playlist' | 'unknown'.
    동일 URL 에 v=... 와 list=... 둘 다 있으면 list 우선 (playlist 우선 ingest).
    """
    u = urlparse(url)
    host = (u.hostname or "").lower()
    if host not in _YT_HOSTS:
        return {"kind": "unknown", "video_id": None, "playlist_id": None}

    qs = parse_qs(u.query)

    if host == "youtu.be":
        vid = u.path.lstrip("/").split("/")[0] or None
        return {
            "kind": "playlist" if qs.get("list") else "video",
            "video_id": vid,
            "playlist_id": qs.get("list", [None])[0],
        }

    if u.path == "/playlist" and "list" in qs:
        return {"kind": "playlist", "video_id": None, "playlist_id": qs["list"][0]}

    vid: str | None = None
    if u.path == "/watch":
        vid = qs.get("v", [None])[0]
    elif u.path.startswith("/shorts/"):
        vid = u.path.split("/shorts/", 1)[1].split("/")[0] or None
    elif u.path.startswith("/embed/"):
        vid = u.path.split("/embed/", 1)[1].split("/")[0] or None

    if vid and qs.get("list"):
        return {"kind": "playlist", "video_id": vid, "playlist_id": qs["list"][0]}
    if vid:
        return {"kind": "video", "video_id": vid, "playlist_id": None}
    if qs.get("list"):
        return {"kind": "playlist", "video_id": None, "playlist_id": qs["list"][0]}
    return {"kind": "unknown", "video_id": None, "playlist_id": None}


# ── yt-dlp / transcript ───────────────────────────────────────


def _ydl_extract(url: str, *, flat: bool = False) -> dict[str, Any]:
    """blocking yt-dlp 호출 — asyncio.to_thread 로 감싸 호출."""
    import yt_dlp

    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": "in_playlist" if flat else False,
        "noplaylist": False if flat else True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return info or {}


async def _ydl_extract_async(url: str, *, flat: bool = False) -> dict[str, Any]:
    return await asyncio.to_thread(_ydl_extract, url, flat=flat)


def _fetch_transcript(video_id: str, languages: tuple[str, ...] = ("ko", "en")) -> str | None:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except Exception as e:  # noqa: BLE001
        logger.warning("youtube-transcript-api import 실패: %s", e)
        return None

    try:
        rows = YouTubeTranscriptApi.get_transcript(video_id, languages=list(languages))
    except Exception as e:  # noqa: BLE001
        # 자막 비활성/없음/disabled by uploader 등 다양한 케이스. info 레벨로만.
        logger.info("자막 없음/실패 (video=%s): %s", video_id, e)
        return None
    return "\n".join(r.get("text", "") for r in rows if r.get("text")).strip() or None


async def _fetch_transcript_async(video_id: str) -> str | None:
    return await asyncio.to_thread(_fetch_transcript, video_id)


def _canonical_video_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def _canonical_playlist_url(playlist_id: str) -> str:
    return f"https://www.youtube.com/playlist?list={playlist_id}"


# ── Single video ──────────────────────────────────────────────


async def ingest_youtube_video(video_url: str, *, analyze_now: bool = True) -> dict[str, Any]:
    parsed = parse_youtube_url(video_url)
    video_id = parsed.get("video_id")
    if not video_id:
        raise ValueError(f"YouTube video URL 이 아닙니다: {video_url}")
    canonical = _canonical_video_url(video_id)

    info = await _ydl_extract_async(canonical, flat=False)
    title = info.get("title") or "(no title)"
    channel = info.get("uploader") or info.get("channel") or ""
    duration = info.get("duration")
    description = (info.get("description") or "").strip()
    upload_date = info.get("upload_date")  # YYYYMMDD
    categories = info.get("categories") or []
    yt_tags = info.get("tags") or []

    transcript = await _fetch_transcript_async(video_id)

    parts = [
        f"YouTube Video: {title}",
        f"URL: {canonical}",
        f"Channel: {channel}",
        f"Duration: {duration}s" if duration else "Duration: unknown",
        f"Uploaded: {upload_date}" if upload_date else "",
        "",
        "## Description",
        description or "(no description)",
    ]
    if transcript:
        parts += ["", "## Transcript", transcript]
    raw_body = "\n".join(p for p in parts if p is not None)

    paper_keywords: list[str] = [*categories, *yt_tags]
    if not transcript:
        paper_keywords.append("no-transcript")

    abstract = transcript if (transcript and len(transcript) >= 100) else (
        description if len(description) >= 100 else None
    )

    doc = ExtractedDoc(
        body=raw_body, title=title, abstract=abstract, paper_keywords=paper_keywords,
    )

    return await _save_with_summary(
        doc=doc,
        source_type="youtube",
        source_id=video_id,
        source_url=canonical,
        source_metadata={
            "kind": "video",
            "video_id": video_id,
            "channel": channel,
            "duration": duration,
            "upload_date": upload_date,
            "categories": categories,
            "yt_tags": yt_tags,
            "has_transcript": bool(transcript),
        },
        analyze_now=analyze_now,
    )


# ── Playlist ──────────────────────────────────────────────────


async def ingest_youtube_playlist(playlist_url: str, *, analyze_now: bool = True) -> dict[str, Any]:
    parsed = parse_youtube_url(playlist_url)
    playlist_id = parsed.get("playlist_id")
    if not playlist_id:
        raise ValueError(f"YouTube playlist URL 이 아닙니다: {playlist_url}")
    canonical = _canonical_playlist_url(playlist_id)

    info = await _ydl_extract_async(canonical, flat=True)
    title = info.get("title") or "(no title)"
    uploader = info.get("uploader") or info.get("channel") or ""
    entries = info.get("entries") or []

    lines: list[str] = [
        f"YouTube Playlist: {title}",
        f"URL: {canonical}",
        f"Uploader: {uploader}",
        f"Total videos: {len(entries)}",
        "",
        "## Videos",
    ]
    for i, e in enumerate(entries, start=1):
        vid_title = e.get("title") or "(no title)"
        vid_id = e.get("id") or ""
        vid_url = e.get("url") or (_canonical_video_url(vid_id) if vid_id else "")
        vid_dur = e.get("duration")
        vid_uploader = e.get("uploader") or ""
        meta = f"({vid_uploader}, {vid_dur}s)" if (vid_uploader or vid_dur) else ""
        lines.append(f"[{i}] {vid_title} {meta} — {vid_url}")
    raw_marker = "## Raw (yt-dlp)"
    lines += ["", raw_marker, _safe_json_dump(info)]
    raw_body = "\n".join(lines)

    abstract_text = "\n".join(lines[: lines.index(raw_marker)])
    paper_keywords = ["youtube-playlist"]

    doc = ExtractedDoc(
        body=raw_body, title=title, abstract=abstract_text,
        paper_keywords=paper_keywords,
    )

    return await _save_with_summary(
        doc=doc,
        source_type="youtube_playlist",
        source_id=playlist_id,
        source_url=canonical,
        source_metadata={
            "kind": "playlist",
            "playlist_id": playlist_id,
            "uploader": uploader,
            "video_count": len(entries),
            "video_ids": [e.get("id") for e in entries],
        },
        analyze_now=analyze_now,
    )


def _safe_json_dump(data: dict[str, Any]) -> str:
    try:
        return json.dumps(data, ensure_ascii=False, default=str, indent=2)
    except Exception:  # noqa: BLE001
        return repr(data)


# ── Dispatcher ────────────────────────────────────────────────


async def ingest_youtube(url: str, *, analyze_now: bool = True) -> dict[str, Any]:
    """URL 의 형태 (video / playlist) 자동 판별 후 적절한 ingester 호출."""
    parsed = parse_youtube_url(url)
    if parsed["kind"] == "playlist":
        return await ingest_youtube_playlist(url, analyze_now=analyze_now)
    if parsed["kind"] == "video":
        return await ingest_youtube_video(url, analyze_now=analyze_now)
    raise ValueError(f"YouTube URL 형식을 판별할 수 없습니다: {url}")


# ── 공통 저장 (url ingest 와 동일 helper 재사용) ──────────────


async def _save_with_summary(
    *,
    doc: ExtractedDoc,
    source_type: str,
    source_id: str,
    source_url: str,
    source_metadata: dict[str, Any],
    analyze_now: bool,
) -> dict[str, Any]:
    if not doc.body or len(doc.body.strip()) < 50:
        raise ValueError("본문이 너무 짧아 저장할 수 없습니다")

    content_hash = sha256_text(doc.body)
    engine = get_engine()
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        existing = await find_item_by_hash(
            session, source_type=source_type, content_hash=content_hash,
        )
        if existing is not None:
            return {"item_id": str(existing), "created": False, "chunks_indexed": 0}

        item_id = await insert_item(
            session,
            source_type=source_type,
            raw_content=doc.body,
            raw_content_hash=content_hash,
            source_id=source_id,
            source_url=source_url,
            source_metadata=source_metadata,
            title=doc.title,
            source_created_at=None,
        )
        await session.commit()

        chunks_indexed = 0
        summary_text: str | None = None
        tags: list[str] = []
        if analyze_now:
            chunks_indexed = await _embed_and_index(session, item_id=item_id, text=doc.body)
            summary_text, tags = await _generate_and_save_summary(
                session, item_id=item_id, doc=doc,
            )

        return {
            "item_id": str(item_id),
            "created": True,
            "chunks_indexed": chunks_indexed,
            "summary_generated": summary_text is not None,
            "tags": tags,
            "title": doc.title,
        }
