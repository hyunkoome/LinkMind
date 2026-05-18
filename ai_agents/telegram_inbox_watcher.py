#!/usr/bin/env python
"""
ai_agents/telegram_inbox_watcher.py
----------------------------------------------------------------------------
Telegram inbox 채널 (예: LinkMind-Inbox) 의 새 메시지를 받아 LinkMind 로 자동
ingest 하는 daemon. ChannelAgent ABC (ai_agents.base) 의 첫 번째 구현체.

CLAUDE.md §3: ai_agents/ 는 LinkMind 의 multi-channel gateway 모듈. backend.ingest.*
모듈을 직접 import 호출하지만 backend.llm.* (LLMProvider) 직접 호출은 금지 — 그건
backend HTTP `/ask` 경유.

설계:
- Telethon 사용자 계정 client. 첫 실행 시 SMS 인증 → session 파일 자동 저장.
- 채널 invite link 로 join 후, NewMessage 이벤트 listener 등록.
- 새 메시지마다 → backend.ingest.telegram.ingest_telegram_message.
- backfill 옵션: `--backfill N` 으로 지난 N개 메시지도 처리.
- inbox 패턴: ingest 성공한 메시지는 채널에서 자동 삭제 (사용자가 처리 안 된 것
  만 시각적으로 확인). `is_ingest_successful` 판정은 ChannelAgent ABC 의 공통 헬퍼.

사용:
    python -m ai_agents.telegram_inbox_watcher                # 실시간 listen
    python -m ai_agents.telegram_inbox_watcher --backfill 50  # 최근 50개도 처리 후 listen
    python -m ai_agents.telegram_inbox_watcher --backfill 50 --no-listen  # backfill 만

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
from typing import Any

# ai_agents/ 가 package 라 backend.* import 위해 프로젝트 루트를 sys.path 에. 표준
# `python -m ai_agents.telegram_inbox_watcher` 호출이면 자동으로 들어가지만, 직접
# `python ai_agents/telegram_inbox_watcher.py` 실행 시도 (legacy) 도 지원.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from ai_agents.base import ChannelAgent  # noqa: E402
from backend.config import Settings, get_settings  # noqa: E402
from backend.ingest.telegram import (  # noqa: E402
    TelegramMessage,
    ingest_telegram_message,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
logger = logging.getLogger("telegram-watcher")


def _check_telethon() -> None:
    try:
        from telethon import TelegramClient, events  # noqa: F401
    except Exception as e:  # noqa: BLE001
        print(f"❌ telethon 미설치: {e}\n   pip install telethon", file=sys.stderr)
        sys.exit(2)


def _check_env() -> Settings:
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

    return await client.get_entity(invite_or_name)


class TelegramChannelAgent(ChannelAgent):
    """Telegram inbox 채널의 ChannelAgent 구현체.

    Telethon 사용자 계정 (bot 아님) 으로 동작 — 봇 API 의 admin 권한 제한 회피.
    inbox 패턴: ingest 성공 시 채널에서 메시지 자동 삭제.
    """

    name = "telegram"

    def __init__(self) -> None:
        self.settings: Settings | None = None
        self.client = None       # telethon.TelegramClient — setup 이후 채워짐
        self.channel = None      # telethon entity — setup 이후 채워짐

    async def setup(self) -> None:
        """env 검증 + Telethon client start + 채널 resolve."""
        _check_telethon()
        self.settings = _check_env()

        from telethon import TelegramClient

        session_path = Path(self.settings.telegram_session_path)
        session_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            api_id_int = int(self.settings.telegram_api_id)
        except ValueError as e:
            raise RuntimeError(
                f"TELEGRAM_API_ID 가 정수가 아님: {self.settings.telegram_api_id!r}"
            ) from e

        self.client = TelegramClient(
            str(session_path), api_id_int, self.settings.telegram_api_hash
        )
        logger.info(
            "Telethon 시작 — session=%s, channel=%s",
            session_path, self.settings.telegram_inbox_invite,
        )
        # start() 가 처음 호출 시 전화번호 + SMS 코드 대화식 입력.
        await self.client.start()
        me = await self.client.get_me()
        logger.info("Telethon 인증 완료 (me=%s)", getattr(me, "username", None) or me.id)

        self.channel = await _resolve_channel(self.client, self.settings.telegram_inbox_invite)
        logger.info(
            "채널 entity: %s (id=%s)",
            getattr(self.channel, "title", self.channel),
            getattr(self.channel, "id", None),
        )

    async def run(self, *, backfill: int = 0, listen: bool = True) -> int:
        """daemon 진입점. setup → backfill → listen 순.

        Args:
            backfill: 채널의 최근 N개 메시지 먼저 처리 (0 = skip).
            listen:   처리 후 실시간 NewMessage stream 계속 (False = backfill 만).
        """
        await self.setup()
        try:
            if backfill > 0:
                await self._backfill(backfill)

            if listen:
                from telethon import events

                @self.client.on(events.NewMessage(chats=self.channel))
                async def _on_new(event):
                    await self._handle_message(event)

                logger.info("listening… (Ctrl+C 로 종료)")
                await self.client.run_until_disconnected()
            else:
                await self.client.disconnect()
        except Exception as e:  # noqa: BLE001
            logger.exception("run 실패: %s", e)
            return 1
        return 0

    async def _backfill(self, count: int) -> None:
        logger.info("backfill 시작 — 최근 %d 개 메시지", count)
        async for msg in self.client.iter_messages(self.channel, limit=count):
            await self._handle_message(msg)
        logger.info("backfill 완료")

    async def _handle_message(self, event_or_msg) -> None:
        """Telethon NewMessage event / iter_messages Message 둘 다 처리.

        NewMessage.Event 는 `.message` 가 Message 객체, iter_messages 는 Message
        자체. `hasattr(event, "message") and hasattr(event.message, "id")` 로 구분.
        """
        if hasattr(event_or_msg, "message") and hasattr(event_or_msg.message, "id"):
            msg = event_or_msg.message     # NewMessage event
        else:
            msg = event_or_msg             # iter_messages 의 Message

        text = (getattr(msg, "message", None) or getattr(msg, "text", None) or "").strip()
        if not text:
            # 사진/파일만 (텍스트 없음) — 일단 skip. 향후 attachment ingest 확장.
            return

        sender = await msg.get_sender()
        sender_name = (
            getattr(sender, "username", None)
            or " ".join(
                filter(None, [getattr(sender, "first_name", None), getattr(sender, "last_name", None)])
            )
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
            permalink=(
                f"https://t.me/c/{channel_id.lstrip('-').removeprefix('100')}/{msg.id}"
                if channel_id else None
            ),
        )

        try:
            result: dict[str, Any] = await ingest_telegram_message(tm, analyze_now=True)
        except Exception as e:  # noqa: BLE001
            logger.exception("ingest 실패 (msg=%s): %s", msg.id, e)
            return

        urls = result.get("urls_ingested") or []
        note = result.get("note_item_id")
        succeeded = self.is_ingest_successful(result)
        logger.info(
            "msg %s: urls=%d note=%s ok=%s text=%r",
            msg.id, len(urls), bool(note), succeeded, text[:80],
        )

        # inbox 패턴 — 성공한 메시지는 채널에서 삭제.
        if succeeded and self.settings and self.settings.telegram_delete_after_ingest:
            try:
                await msg.delete()
                logger.info("msg %s ingest 성공 → 채널에서 삭제", msg.id)
            except Exception as e:  # noqa: BLE001
                logger.warning("msg %s 삭제 실패 (권한/네트워크?): %s", msg.id, e)


if __name__ == "__main__":
    p = argparse.ArgumentParser(prog="telegram_inbox_watcher")
    p.add_argument("--backfill", type=int, default=0,
                   help="채널의 최근 N개 메시지를 먼저 ingest")
    p.add_argument("--no-listen", action="store_true",
                   help="backfill 만 하고 종료 (listen 단계 skip)")
    args = p.parse_args()

    agent = TelegramChannelAgent()
    rc = asyncio.run(agent.run(backfill=args.backfill, listen=not args.no_listen))
    sys.exit(rc)
