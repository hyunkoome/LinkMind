#!/usr/bin/env python
"""
scripts/telegram_inbox_watcher.py
----------------------------------------------------------------------------
Telegram inbox 채널 (예: LinkMind-Inbox) 의 새 메시지를 받아 LinkMind 로 자동
ingest 하는 별 process daemon.

CLAUDE.md §3: backend 안에 봇 코드는 두지 않는다. 이 watcher 는 backend 외부
(scripts/) 에 있고 backend.ingest.telegram 의 parser/ingest 함수를 호출만 한다.

설계:
- Telethon 사용자 계정 client. 첫 실행 시 SMS 인증 → session 파일 자동 저장.
- 채널 invite link 로 join 후, NewMessage 이벤트 listener 등록.
- 새 메시지마다 → backend.ingest.telegram.ingest_telegram_message.
- 채널 history backfill 옵션: `--backfill N` 으로 지난 N개 메시지도 처리.

사용:
    python scripts/telegram_inbox_watcher.py             # 실시간 listen
    python scripts/telegram_inbox_watcher.py --backfill 50  # 최근 50개도 처리 후 listen
    python scripts/telegram_inbox_watcher.py --backfill 50 --no-listen  # backfill 만

환경변수 (env/dev.env):
    TELEGRAM_API_ID         my.telegram.org 에서 발급
    TELEGRAM_API_HASH       my.telegram.org 에서 발급
    TELEGRAM_SESSION_PATH   session 파일 위치 (기본: volumes/telegram/inbox.session)
    TELEGRAM_INBOX_INVITE   채널 invite link (예: https://t.me/+abc) 또는 채널명/id
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import get_settings  # noqa: E402
from backend.ingest.telegram import TelegramMessage, ingest_telegram_message  # noqa: E402


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
logger = logging.getLogger("telegram-watcher")


def _check_telethon():
    try:
        from telethon import TelegramClient, events  # noqa: F401
    except Exception as e:  # noqa: BLE001
        print(f"❌ telethon 미설치: {e}\n   pip install telethon", file=sys.stderr)
        sys.exit(2)


def _check_env():
    s = get_settings()
    missing: list[str] = []
    if not s.telegram_api_id:
        missing.append("TELEGRAM_API_ID")
    if not s.telegram_api_hash:
        missing.append("TELEGRAM_API_HASH")
    if not s.telegram_inbox_invite:
        missing.append("TELEGRAM_INBOX_INVITE")
    if missing:
        print(
            f"❌ env 미설정: {', '.join(missing)}\n"
            f"   env/dev.env 채워 넣은 후 재실행 — "
            f"https://my.telegram.org 에서 API ID/Hash 발급.",
            file=sys.stderr,
        )
        sys.exit(2)
    return s


async def _resolve_channel(client, invite_or_name: str):
    """invite link 면 join 후 entity 반환. 채널명/id 면 그대로 get_entity.

    Telethon 의 get_entity 가 invite link 직접 처리 못 함 — ImportChatInviteRequest
    또는 CheckChatInviteRequest 가 필요. 단순화 위해 join 시도 후 fallback.
    """
    from telethon.errors import (
        InviteHashExpiredError,
        InviteHashInvalidError,
        UserAlreadyParticipantError,
    )
    from telethon.tl.functions.messages import (
        CheckChatInviteRequest,
        ImportChatInviteRequest,
    )

    if invite_or_name.startswith(("https://t.me/+", "https://t.me/joinchat/", "t.me/+")):
        hash_part = invite_or_name.rsplit("/", 1)[-1].lstrip("+")
        # 이미 join 한 채널 이면 ImportChatInviteRequest 가 UserAlreadyParticipantError.
        try:
            r = await client(ImportChatInviteRequest(hash_part))
            return r.chats[0]
        except UserAlreadyParticipantError:
            # 이미 멤버 — CheckChatInviteRequest 로 entity 조회.
            check = await client(CheckChatInviteRequest(hash_part))
            chat = check.chat if hasattr(check, "chat") else check.chats[0]
            return await client.get_entity(chat)
        except (InviteHashInvalidError, InviteHashExpiredError) as e:
            raise RuntimeError(f"invite link 만료/유효하지 않음: {e}") from e

    # 채널 username (@foo) 또는 peer id 의 경우
    return await client.get_entity(invite_or_name)


async def _handle_message(event):
    """Telethon NewMessage / iter_messages 가 주는 message 객체 → LinkMind."""
    msg = event.message if hasattr(event, "message") else event
    text = (msg.message or "").strip()
    if not text:
        # 사진/파일만 (텍스트 없음) — 일단 skip. 향후 attachment ingest 확장.
        return
    sender = await msg.get_sender()
    sender_name = (
        getattr(sender, "username", None)
        or " ".join(filter(None, [getattr(sender, "first_name", None), getattr(sender, "last_name", None)]))
        or None
    )
    chat = await msg.get_chat()
    channel_id = str(getattr(chat, "id", "") or "")
    channel_name = getattr(chat, "title", None) or getattr(chat, "username", None)

    tm = TelegramMessage(
        msg_id=msg.id,
        date=msg.date if isinstance(msg.date, datetime) else None,
        text=text,
        sender=sender_name,
        sender_id=str(getattr(sender, "id", "") or "") or None,
        channel=channel_name,
        channel_id=channel_id or None,
        permalink=f"https://t.me/c/{channel_id.lstrip('-').removeprefix('100')}/{msg.id}"
        if channel_id else None,
    )

    try:
        result = await ingest_telegram_message(tm, analyze_now=True)
    except Exception as e:  # noqa: BLE001
        logger.exception("ingest 실패 (msg=%s): %s", msg.id, e)
        return

    urls = result.get("urls_ingested") or []
    note = result.get("note_item_id")
    logger.info(
        "msg %s: urls=%d note=%s text=%r",
        msg.id, len(urls), bool(note), text[:80],
    )


async def _backfill(client, channel, count: int):
    logger.info("backfill 시작 — 최근 %d 개 메시지", count)
    async for msg in client.iter_messages(channel, limit=count):
        await _handle_message(msg)
    logger.info("backfill 완료")


async def _run(backfill: int, listen: bool) -> int:
    _check_telethon()
    s = _check_env()

    from telethon import TelegramClient, events
    session_path = Path(s.telegram_session_path)
    session_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        api_id_int = int(s.telegram_api_id)
    except ValueError:
        print(f"❌ TELEGRAM_API_ID 가 정수가 아님: {s.telegram_api_id!r}", file=sys.stderr)
        return 2
    client = TelegramClient(
        str(session_path),
        api_id_int,
        s.telegram_api_hash,
    )
    logger.info(
        "Telethon 시작 — session=%s, channel=%s",
        session_path, s.telegram_inbox_invite,
    )

    # start() 는 처음 호출 시 전화번호 + SMS 코드 대화식 입력.
    await client.start()
    logger.info("Telethon 인증 완료 (me=%s)", (await client.get_me()).username)

    try:
        channel = await _resolve_channel(client, s.telegram_inbox_invite)
        logger.info("채널 entity: %s (id=%s)", getattr(channel, "title", channel), getattr(channel, "id", None))
    except Exception as e:  # noqa: BLE001
        logger.error("채널 resolve 실패: %s", e)
        await client.disconnect()
        return 1

    if backfill > 0:
        await _backfill(client, channel, backfill)

    if listen:
        @client.on(events.NewMessage(chats=channel))
        async def _on_new(event):
            await _handle_message(event)

        logger.info("listening… (Ctrl+C 로 종료)")
        await client.run_until_disconnected()
    else:
        await client.disconnect()

    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(prog="telegram_inbox_watcher")
    p.add_argument("--backfill", type=int, default=0,
                   help="채널의 최근 N개 메시지를 먼저 ingest")
    p.add_argument("--no-listen", action="store_true",
                   help="backfill 만 하고 종료 (listen 단계 skip)")
    args = p.parse_args()

    rc = asyncio.run(_run(backfill=args.backfill, listen=not args.no_listen))
    sys.exit(rc)
