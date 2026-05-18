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
    python -m ai_agents.telegram_inbox_watcher                  # 자동 backfill (안 지워진
                                                               # 모든 메시지) → listen
    python -m ai_agents.telegram_inbox_watcher --no-backfill   # backfill 없이 listen 만
    python -m ai_agents.telegram_inbox_watcher --backfill 50 --no-listen  # backfill 만 (50개)

기본 동작 (Phase 2.5 wave-3, 2026-05-18~):
- daemon 시작 시 채널에 남아있는 모든 메시지 자동 backfill (inbox 패턴 — 처리
  성공한 메시지는 채널에서 자동 삭제되므로, "남아있다 = 아직 처리 안 됨").
- backfill 끝나면 listen 시작 (이후 새 메시지 자동).
- 매번 daemon 재시작 시 같은 흐름. 이미 처리된 메시지는 채널에 없으니 backfill skip.
- ingested_at = ingest 시각 (현재). source_created_at = 텔레그램 메시지 원본 시각 (provenance).

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
import tempfile
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
    TelegramAttachment,
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
        """채널의 메시지 backfill — 옛 → 새 순서 (queue 처럼).

        count 가 0 이하면 skip. 큰 수 (예: 10000) 면 사실상 전체 처리.

        inbox 패턴이라 이미 ingest 된 메시지는 채널에서 자동 삭제 — 다음 시작 시
        backfill 대상은 "아직 처리 안 된 것" 만 자연스럽게 남음.

        reverse=True 로 옛 메시지부터 처리 — 사용자가 채널에 던진 순서 보존.
        """
        if count <= 0:
            logger.info("backfill skip (count=%d)", count)
            return
        logger.info("backfill 시작 — 최근 %d 개 메시지 (옛→새 순서)", count)
        processed = 0
        async for msg in self.client.iter_messages(self.channel, limit=count, reverse=True):
            await self._handle_message(msg)
            processed += 1
        logger.info("backfill 완료 — %d 메시지 처리", processed)

    async def _handle_message(self, event_or_msg) -> None:
        """Telethon NewMessage event / iter_messages Message 둘 다 처리.

        Phase 2.5 wave-3 — 첨부 (PDF/DOCX/PPTX/TXT/MD/이미지/zip 등) 자동 download
        후 ingest_document 로 보냄. 텍스트 + 첨부 + URL 모두 가능 (한 메시지에).

        NewMessage.Event 는 `.message` 가 Message 객체, iter_messages 는 Message
        자체. `hasattr(event, "message") and hasattr(event.message, "id")` 로 구분.
        """
        if hasattr(event_or_msg, "message") and hasattr(event_or_msg.message, "id"):
            msg = event_or_msg.message     # NewMessage event
        else:
            msg = event_or_msg             # iter_messages 의 Message

        text = (getattr(msg, "message", None) or getattr(msg, "text", None) or "").strip()
        attachments_local, tmp_dir = await self._download_attachments(msg)

        # 텍스트도 없고 첨부도 없으면 skip (Telegram 의 system message / reaction 등)
        if not text and not attachments_local:
            self._cleanup_tmp_dir(tmp_dir)
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
            attachments=attachments_local,
        )

        try:
            result: dict[str, Any] = await ingest_telegram_message(tm, analyze_now=True)
        except Exception as e:  # noqa: BLE001
            logger.exception("ingest 실패 (msg=%s): %s", msg.id, e)
            self._cleanup_tmp_dir(tmp_dir)
            return
        finally:
            # storage 는 sha256 dedup 으로 영구 복사됐으므로 tmp 정리 안전.
            self._cleanup_tmp_dir(tmp_dir)

        urls = result.get("urls_ingested") or []
        atts = result.get("attachments_ingested") or []
        note = result.get("note_item_id")
        succeeded = self.is_ingest_successful(result)
        logger.info(
            "msg %s: urls=%d attach=%d note=%s ok=%s text=%r",
            msg.id, len(urls), len(atts), bool(note), succeeded, text[:80],
        )

        # inbox 패턴 — 모든 ingest 가 성공해야만 채널에서 삭제.
        # is_ingest_successful 이 urls + attachments + note 의 error 부재 확인 (§ChannelAgent).
        if succeeded and self.settings and self.settings.telegram_delete_after_ingest:
            try:
                await msg.delete()
                logger.info("msg %s ingest 성공 → 채널에서 삭제", msg.id)
            except Exception as e:  # noqa: BLE001
                logger.warning("msg %s 삭제 실패 (권한/네트워크?): %s", msg.id, e)

    async def _download_attachments(
        self, msg,
    ) -> tuple[list[TelegramAttachment], Path | None]:
        """Telethon msg 에서 첨부 파일들을 임시 디렉토리에 download.

        Photo / Document (PDF/DOCX/이미지/zip 등) 모두 download_media 로 동일하게
        받음. web_preview (URL preview) 는 첨부 아님 — msg.file 이 None 이거나
        webpage 타입이라 자동 skip.

        반환: (TelegramAttachment list, 임시 디렉토리 경로 or None)
        """
        file_obj = getattr(msg, "file", None)
        if file_obj is None:
            return [], None

        # web preview 는 file 객체가 None 이거나 attribute 없음 — 위에서 걸러짐.
        tmp_dir = Path(tempfile.mkdtemp(prefix="linkmind_tg_"))
        try:
            downloaded_path = await msg.download_media(file=str(tmp_dir))
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "msg %s 첨부 download 실패 (%s: %s) — 메시지 보존",
                msg.id, type(e).__name__, e,
            )
            self._cleanup_tmp_dir(tmp_dir)
            return [], None

        if not downloaded_path:
            self._cleanup_tmp_dir(tmp_dir)
            return [], None

        dp = Path(downloaded_path)
        if not dp.exists():
            self._cleanup_tmp_dir(tmp_dir)
            return [], None

        mime_type = getattr(file_obj, "mime_type", None)
        file_name = getattr(file_obj, "name", None) or dp.name
        file_size = dp.stat().st_size

        return [
            TelegramAttachment(
                file_path=str(dp),
                file_name=file_name,
                mime_type=mime_type,
                size=file_size,
            )
        ], tmp_dir

    @staticmethod
    def _cleanup_tmp_dir(tmp_dir: Path | None) -> None:
        """임시 다운로드 디렉토리 + 안의 파일 정리. storage 는 이미 복사됨."""
        if not tmp_dir:
            return
        try:
            for child in tmp_dir.iterdir():
                child.unlink(missing_ok=True)
            tmp_dir.rmdir()
        except Exception as e:  # noqa: BLE001
            logger.debug("tmp_dir cleanup 실패 (무시): %s", e)


# 사실상 무한 — inbox 채널이라 처리 후 자동 삭제, 남은 메시지 다 처리하는 게
# default. 사용자 환경의 채널이 매우 크면 (>10000) `--backfill` 로 명시 override.
_DEFAULT_BACKFILL = 10000


if __name__ == "__main__":
    p = argparse.ArgumentParser(prog="telegram_inbox_watcher")
    p.add_argument("--backfill", type=int, default=_DEFAULT_BACKFILL,
                   help=f"채널의 최근 N개 메시지를 먼저 ingest (default {_DEFAULT_BACKFILL}, "
                        "사실상 채널에 남아있는 모든 메시지)")
    p.add_argument("--no-backfill", action="store_true",
                   help="backfill 완전 skip (--backfill 0 과 같음)")
    p.add_argument("--no-listen", action="store_true",
                   help="backfill 만 하고 종료 (listen 단계 skip)")
    args = p.parse_args()

    backfill_count = 0 if args.no_backfill else args.backfill

    agent = TelegramChannelAgent()
    rc = asyncio.run(agent.run(backfill=backfill_count, listen=not args.no_listen))
    sys.exit(rc)
