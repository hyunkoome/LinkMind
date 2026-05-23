#!/usr/bin/env python
"""
ai_agents/telegram_inbox_watcher.py
----------------------------------------------------------------------------
Telegram inbox 채널들의 새 메시지를 받아 LinkMind 로 자동 ingest 하는 daemon.
multi-channel 지원 (2026-05-23) — config/telegram_channels.yaml 의 모든 채널을
한 watcher 가 동시에 listen + backfill.

CLAUDE.md §3: ai_agents/ 는 LinkMind 의 multi-channel gateway 모듈. backend.ingest.*
모듈을 직접 import 호출하지만 backend.llm.* (LLMProvider) 직접 호출은 금지 — 그건
backend HTTP `/ask` 경유.

설계:
- Telethon 사용자 계정 client. 첫 실행 시 SMS 인증 → session 파일 자동 저장.
- yaml 의 채널 list 모두 join 후, NewMessage(chats=list) 로 통합 listener 등록.
- 새 메시지마다 → 어느 채널인지 chat.id 로 식별 → 채널별 delete 정책 적용.
- backfill 옵션: `--backfill N` 으로 지난 N개 메시지도 처리 (모든 채널 순차).
- inbox 패턴: ingest 성공한 메시지는 채널의 정책에 따라 자동 삭제. 채널별
  delete_after_ingest=true/false 를 yaml 에서 설정.

사용:
    python -m ai_agents.telegram_inbox_watcher                  # 자동 backfill → listen
    python -m ai_agents.telegram_inbox_watcher --no-backfill   # backfill 없이 listen 만
    python -m ai_agents.telegram_inbox_watcher --backfill 50 --no-listen  # backfill 만

기본 동작:
- daemon 시작 시 각 채널에 남아있는 모든 메시지 자동 backfill (inbox 패턴).
- backfill 끝나면 모든 채널 동시 listen.
- ingested_at = ingest 시각. source_created_at = 텔레그램 메시지 원본 시각 (provenance).

환경변수 (env/dev.env):
    TELEGRAM_API_ID            my.telegram.org 에서 발급
    TELEGRAM_API_HASH          my.telegram.org 에서 발급
    TELEGRAM_SESSION_PATH      session 파일 위치 (기본: volumes/telegram/inbox.session)
    TELEGRAM_CHANNELS_CONFIG   yaml 채널 list 경로 (기본: config/telegram_channels.yaml)
    TELEGRAM_INBOX_INVITE      단일 채널 fallback (yaml 미존재 시)
    TELEGRAM_DELETE_AFTER_INGEST  fallback 채널의 default delete 정책
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import tempfile
from dataclasses import dataclass
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
from ai_agents.telegram_channels import (  # noqa: E402
    TelegramChannelConfig,
    TelegramWatcherConfig,
    load_telegram_channels,
)
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
    if missing:
        print(
            f"❌ env 미설정: {', '.join(missing)}\n"
            f"   env/dev.env 채워 넣은 후 재실행 — "
            f"https://my.telegram.org 에서 API ID/Hash 발급.",
            file=sys.stderr,
        )
        sys.exit(2)
    return s


def _load_watcher_config(s: Settings) -> TelegramWatcherConfig:
    """yaml 단일 진실 (2026-05-23). 채널 0개면 sys.exit (운영자가 yaml 채우도록 강제)."""
    cfg = load_telegram_channels(s.telegram_channels_config)
    if not cfg.channels:
        print(
            "❌ 텔레그램 채널 설정 없음.\n"
            f"   {s.telegram_channels_config} 의 channels: list 에 invite + delete_after_ingest "
            f"항목 채워 넣고 재실행.",
            file=sys.stderr,
        )
        sys.exit(2)
    return cfg


# FloodWait 임계값 — 이보다 길면 skip (운영자가 다음 재기동 시 처리).
# 텔레그램 API rate limit 은 보통 300초 (5분) 이하라 5분 임계가 합리.
_FLOOD_WAIT_MAX_S = 300

# 채널 resolve (join) 사이 sleep — Telegram rate limit 예방. 보통 2-3초로 충분.
_CHANNEL_RESOLVE_SLEEP_S = 3.0


def _invite_hash(invite_or_name: str) -> str | None:
    """invite link 면 hash 부분 (cache key) 반환. 채널명/id 면 None."""
    if invite_or_name.startswith(("https://t.me/+", "https://t.me/joinchat/", "t.me/+")):
        return invite_or_name.rsplit("/", 1)[-1].lstrip("+")
    return None


def _load_channel_id_cache(path: Path) -> dict[str, int]:
    """invite hash → channel_id 영구 cache 로드. 파일 미존재 시 빈 dict."""
    if not path.exists():
        return {}
    try:
        import json
        return {k: int(v) for k, v in json.loads(path.read_text(encoding="utf-8")).items()}
    except Exception as e:  # noqa: BLE001
        logger.warning("channel_id cache 로드 실패 (%s, 무시): %s", path, e)
        return {}


def _save_channel_id_cache(path: Path, cache: dict[str, int]) -> None:
    """cache atomic 저장 (tmp + rename)."""
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


async def _resolve_channel(
    client,
    invite_or_name: str,
    *,
    channel_id_cache: dict[str, int] | None = None,
    dialog_entities: dict[int, object] | None = None,
):
    """채널 entity 반환. resolve 우선순위:

    1. invite hash → channel_id cache → dialog_entities 안에서 lookup (rate limit X)
    2. cache → client.get_entity(channel_id) (rate limit X)
    3. ImportChatInviteRequest (rate limit 영향)
    4. UserAlreadyParticipant → CheckChatInviteRequest fallback

    FloodWaitError 가 _FLOOD_WAIT_MAX_S 이하면 자동 sleep + 재시도, 그 이상이면
    RuntimeError 로 올려서 호출자가 skip 결정.

    cache 와 dialog_entities 는 호출자가 한 번 로드/생성 → 모든 채널 resolve 에 공유.
    """
    from telethon.errors import (
        FloodWaitError,
        InviteHashExpiredError,
        InviteHashInvalidError,
        UserAlreadyParticipantError,
    )
    from telethon.tl.functions.messages import (
        CheckChatInviteRequest,
        ImportChatInviteRequest,
    )

    hash_part = _invite_hash(invite_or_name)

    # 1. invite cache + dialog 매칭 (rate limit 완전 회피).
    if hash_part and channel_id_cache is not None and hash_part in channel_id_cache:
        chan_id = channel_id_cache[hash_part]
        if dialog_entities and chan_id in dialog_entities:
            return dialog_entities[chan_id]
        # cache 에 있지만 dialog 에 없을 수도 — get_entity 시도 (보통 rate limit X)
        try:
            return await client.get_entity(chan_id)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "cache hit 인 channel_id=%s 의 get_entity 실패 (cache 무효화): %s",
                chan_id, e,
            )
            # cache 오염 — 제거 후 fallthrough 로 ImportChatInvite 재시도.
            channel_id_cache.pop(hash_part, None)

    if hash_part is None:
        # 채널명/peer id 직접 (rate limit 보통 없음).
        return await client.get_entity(invite_or_name)

    # 2. ImportChatInvite (rate limit risk).
    for attempt in range(2):  # 최대 1회 재시도 (FloodWait 자동 대기 후).
        try:
            r = await client(ImportChatInviteRequest(hash_part))
            return r.chats[0]
        except UserAlreadyParticipantError:
            # 이미 멤버 — CheckChatInviteRequest 로 entity 조회.
            try:
                check = await client(CheckChatInviteRequest(hash_part))
            except FloodWaitError as e:
                if e.seconds <= _FLOOD_WAIT_MAX_S and attempt == 0:
                    logger.info(
                        "FloodWait %ds 자동 대기 — invite=%s (CheckChatInvite)",
                        e.seconds, invite_or_name,
                    )
                    await asyncio.sleep(e.seconds + 1)
                    continue
                raise
            chat = check.chat if hasattr(check, "chat") else check.chats[0]
            return await client.get_entity(chat)
        except FloodWaitError as e:
            if e.seconds <= _FLOOD_WAIT_MAX_S and attempt == 0:
                logger.info(
                    "FloodWait %ds 자동 대기 — invite=%s (ImportChatInvite)",
                    e.seconds, invite_or_name,
                )
                await asyncio.sleep(e.seconds + 1)
                continue
            raise
        except (InviteHashInvalidError, InviteHashExpiredError) as e:
            raise RuntimeError(f"invite link 만료/유효하지 않음: {e}") from e
    # 여기 도달 시 재시도 끝 — 마지막 시도가 FloodWait 였을 것.
    raise RuntimeError(f"FloodWait 재시도 후에도 resolve 실패 — invite={invite_or_name}")


@dataclass
class ResolvedChannel:
    """resolve 후 — telethon entity + 운영 정책 + 표시 이름."""
    config: TelegramChannelConfig
    entity: object   # telethon Chat/Channel entity
    chat_id: int     # 빠른 lookup 용 (events 의 chat.id 매칭)
    display: str     # 로그 표시용 (yaml name 우선, 없으면 entity.title)


class TelegramChannelAgent(ChannelAgent):
    """Telegram multi-channel inbox ChannelAgent 구현체.

    Telethon 사용자 계정 (bot 아님) 으로 동작 — 봇 API 의 admin 권한 제한 회피.
    inbox 패턴: 채널별 yaml 설정 (delete_after_ingest) 에 따라 ingest 성공 시
    메시지 자동 삭제.

    yaml 의 모든 채널을 한 client 가 통합 listen — events.NewMessage(chats=list)
    로 entity list 전달. 메시지 도착 시 chat.id 로 어느 채널인지 식별.
    """

    name = "telegram"

    def __init__(self, *, analyze_now: bool = False) -> None:
        """default 가 analyze_now=False (사용자 architecture 비판 2026-05-18 반영).

        텔레그램은 사용자 입력 channel — 채널 빠르게 비우는 게 우선. raw + 채널
        삭제만 즉시, chunks (embedding) + summary (LLM) 는 backend 의 analysis_worker
        가 백그라운드로 천천히 처리.

        batch_size 는 yaml 의 batch_size 필드 (default 5). setup() 에서 자동 결정.
        """
        self.settings: Settings | None = None
        self.client = None       # telethon.TelegramClient — setup 이후 채워짐
        self.watcher_config: TelegramWatcherConfig | None = None  # setup 이후
        self.channels: list[ResolvedChannel] = []  # 누적 resolve 결과
        self._by_chat_id: dict[int, ResolvedChannel] = {}
        self.analyze_now = analyze_now
        # invite hash → channel_id 영구 cache + dialog entity dict — setup() 에서 채움.
        # rate limit 회피 위해 _resolve_channel 가 우선 lookup.
        self._channel_id_cache: dict[str, int] = {}
        self._channel_id_cache_path: Path | None = None
        self._dialog_entities: dict[int, object] = {}

    @property
    def channel_configs(self) -> list[TelegramChannelConfig]:
        """yaml 의 채널 list (편의 접근)."""
        return self.watcher_config.channels if self.watcher_config else []

    @property
    def batch_size(self) -> int:
        return self.watcher_config.batch_size if self.watcher_config else 5

    async def setup(self) -> None:
        """env 검증 + yaml 로드 + Telethon client start. 채널 resolve 는 run() 에서 batch 별로."""
        _check_telethon()
        self.settings = _check_env()
        self.watcher_config = _load_watcher_config(self.settings)

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
            "Telethon 시작 — session=%s, channels=%d (batch=%d)",
            session_path, len(self.channel_configs), self.batch_size,
        )
        # start() 가 처음 호출 시 전화번호 + SMS 코드 대화식 입력.
        await self.client.start()
        me = await self.client.get_me()
        logger.info("Telethon 인증 완료 (me=%s)", getattr(me, "username", None) or me.id)

        # invite hash → channel_id 영구 cache 로드 (rate limit 회피 핵심).
        self._channel_id_cache_path = session_path.parent / "channel_id_cache.json"
        self._channel_id_cache = _load_channel_id_cache(self._channel_id_cache_path)
        logger.info(
            "channel_id cache: %d entries (%s)",
            len(self._channel_id_cache), self._channel_id_cache_path,
        )

        # 사용자가 이미 join 한 모든 dialog (채널) 한 번에 조회 — rate limit 가벼움.
        # cache 안에 있는 channel_id 의 entity 를 즉시 lookup 가능 (ImportChatInvite 회피).
        try:
            dialogs = await self.client.get_dialogs(limit=500)
            for d in dialogs:
                ent = d.entity
                if hasattr(ent, "id"):
                    self._dialog_entities[int(ent.id)] = ent
            logger.info(
                "dialog cache: %d channels (이미 join 한 채널은 rate limit 회피 가능)",
                len(self._dialog_entities),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "get_dialogs 실패 (cache 없이 진행): %s: %s", type(e).__name__, e,
            )

    async def _resolve_one(self, cfg: TelegramChannelConfig) -> ResolvedChannel | None:
        """한 채널 join + 등록. 실패 시 None (호출자가 warn + skip).

        cache + dialog lookup 우선 → rate limit 회피. 새 채널만 ImportChatInvite.
        성공 시 cache 업데이트 (다음 실행에서 rate limit 영향 없도록).
        """
        try:
            entity = await _resolve_channel(
                self.client, cfg.invite,
                channel_id_cache=self._channel_id_cache,
                dialog_entities=self._dialog_entities,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "채널 resolve 실패 — skip (invite=%s): %s: %s",
                cfg.invite, type(e).__name__, e,
            )
            return None
        chat_id = int(getattr(entity, "id", 0))
        display = cfg.name or getattr(entity, "title", None) or cfg.display_name()
        resolved = ResolvedChannel(config=cfg, entity=entity, chat_id=chat_id, display=display)
        self.channels.append(resolved)
        self._by_chat_id[chat_id] = resolved

        # cache 업데이트 — 다음 실행은 ImportChatInvite skip.
        hash_part = _invite_hash(cfg.invite)
        if hash_part and chat_id and self._channel_id_cache.get(hash_part) != chat_id:
            self._channel_id_cache[hash_part] = chat_id
            if self._channel_id_cache_path:
                try:
                    _save_channel_id_cache(self._channel_id_cache_path, self._channel_id_cache)
                except Exception as e:  # noqa: BLE001
                    logger.warning("channel_id cache 저장 실패 (무시): %s", e)

        logger.info(
            "채널 등록: %s (id=%s, delete=%s)",
            display, chat_id, cfg.delete_after_ingest,
        )
        return resolved

    async def run(self, *, backfill: int = 0, listen: bool = True) -> int:
        """daemon 진입점. batch sequential — [join batch → backfill batch → 다음].

        Args:
            backfill: 채널별 최근 N개 메시지 먼저 처리 (0 = skip, batch 도 skip).
            listen:   모든 batch 끝난 후 통합 listen (False = backfill 만 하고 종료).

        batch 패턴 이유 (사용자 결정 2026-05-23):
        1. Telegram join rate limit 회피 (한 batch 안의 join 사이에 sleep, batch 간
           긴 sleep + backfill 자체가 시간 buffer).
        2. backend 부하 분산 — 한 batch 의 backfill 다 끝나야 다음 채널 메시지 쏟아짐.
        3. progress 가시화 — "batch 1/3 완료" 로그가 사용자 시각적 진척도.

        backfill=0 일 때는 batch 안 나누고 한 번에 모두 join 후 listen (단순).
        """
        await self.setup()
        try:
            if backfill > 0 and len(self.channel_configs) > self.batch_size:
                # batch sequential 모드 — yaml 의 채널을 batch_size 씩 나눠 처리.
                batches = [
                    self.channel_configs[i:i + self.batch_size]
                    for i in range(0, len(self.channel_configs), self.batch_size)
                ]
                total = len(batches)
                for bi, batch_cfgs in enumerate(batches, start=1):
                    logger.info("─── batch %d/%d 시작 — %d 채널 ───", bi, total, len(batch_cfgs))
                    batch_resolved: list[ResolvedChannel] = []
                    for idx, cfg in enumerate(batch_cfgs):
                        if idx > 0:
                            await asyncio.sleep(_CHANNEL_RESOLVE_SLEEP_S)
                        rc = await self._resolve_one(cfg)
                        if rc:
                            batch_resolved.append(rc)
                    # 이 batch 의 채널들 backfill 완주 — 다음 batch 전에.
                    for rc in batch_resolved:
                        await self._backfill_channel(rc, backfill)
                    logger.info("─── batch %d/%d 완료 ───", bi, total)
                    if bi < total:
                        # 다음 batch 전 짧은 buffer — backfill 이 이미 시간 buffer 역할.
                        await asyncio.sleep(_BATCH_BREATHE_S)
            else:
                # backfill 안 하거나 채널 수 적으면 단순 모드 — 한 번에 다 join.
                for idx, cfg in enumerate(self.channel_configs):
                    if idx > 0:
                        await asyncio.sleep(_CHANNEL_RESOLVE_SLEEP_S)
                    rc = await self._resolve_one(cfg)
                    if rc and backfill > 0:
                        await self._backfill_channel(rc, backfill)

            if not self.channels:
                logger.error("resolve 된 채널이 0개 — listen 불가. yaml 또는 네트워크 확인.")
                return 1

            if listen:
                from telethon import events

                # entity list 를 그대로 전달 — Telethon 가 multi-chat filter 지원.
                entities = [rc.entity for rc in self.channels]

                @self.client.on(events.NewMessage(chats=entities))
                async def _on_new(event):
                    await self._handle_message(event)

                logger.info("listening… %d 채널 동시 (Ctrl+C 로 종료)", len(self.channels))
                await self.client.run_until_disconnected()
            else:
                await self.client.disconnect()
        except Exception as e:  # noqa: BLE001
            logger.exception("run 실패: %s", e)
            return 1
        return 0

    async def _backfill_channel(self, rc: ResolvedChannel, count: int) -> None:
        """한 채널의 메시지 backfill — 옛 → 새 순서 (queue 처럼).

        count 가 0 이하면 skip. 큰 수 (예: 10000) 면 사실상 전체 처리.

        inbox 패턴이라 이미 ingest 된 메시지는 채널에서 자동 삭제 — 다음 시작 시
        backfill 대상은 "아직 처리 안 된 것" 만 자연스럽게 남음.

        reverse=True 로 옛 메시지부터 처리 — 사용자가 채널에 던진 순서 보존.
        """
        if count <= 0:
            return
        logger.info("[%s] backfill 시작 — 최근 %d 개 메시지 (옛→새)", rc.display, count)
        processed = 0
        async for msg in self.client.iter_messages(rc.entity, limit=count, reverse=True):
            await self._handle_message(msg, _rc_hint=rc)
            processed += 1
        logger.info("[%s] backfill 완료 — %d 메시지 처리", rc.display, processed)

    async def _handle_message(
        self, event_or_msg, *, _rc_hint: "ResolvedChannel | None" = None,
    ) -> None:
        """Telethon NewMessage event / iter_messages Message 둘 다 처리.

        Phase 2.5 wave-3 — 첨부 (PDF/DOCX/PPTX/TXT/MD/이미지/zip 등) 자동 download
        후 ingest_document 로 보냄. 텍스트 + 첨부 + URL 모두 가능 (한 메시지에).

        multi-channel (2026-05-23): chat.id 로 어느 채널인지 식별 → 채널별
        delete_after_ingest 정책 적용. _rc_hint 는 backfill 시 채널 미리 알고 있으니
        lookup 생략 위해 전달 (listen 분기는 None).
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
            result: dict[str, Any] = await ingest_telegram_message(
                tm, analyze_now=self.analyze_now,
            )
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

        # 어느 채널에서 온 메시지인지 식별 — backfill 은 hint 로 즉시 알고,
        # listen 분기는 chat.id 로 lookup. 매칭 실패 시 (이론적으론 없음) 안전 default
        # 는 삭제 안 함 (rollback 가능). 정상은 항상 yaml 의 채널 정책.
        rc = _rc_hint or self._by_chat_id.get(int(getattr(chat, "id", 0)))
        ch_display = rc.display if rc else (channel_name or "unknown")
        delete_policy = rc.config.delete_after_ingest if rc else False

        logger.info(
            "[%s] msg %s: urls=%d attach=%d note=%s ok=%s text=%r",
            ch_display, msg.id, len(urls), len(atts), bool(note), succeeded, text[:80],
        )

        # inbox 패턴 — 모든 ingest 가 성공해야만 채널에서 삭제.
        # is_ingest_successful 이 urls + attachments + note 의 error 부재 확인 (§ChannelAgent).
        if succeeded and delete_policy:
            try:
                await msg.delete()
                logger.info("[%s] msg %s ingest 성공 → 채널에서 삭제", ch_display, msg.id)
            except Exception as e:  # noqa: BLE001
                logger.warning("[%s] msg %s 삭제 실패 (권한/네트워크?): %s", ch_display, msg.id, e)

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
    p.add_argument("--fast", action="store_true",
                   help="LLM 요약/태그 skip — raw + chunks (임베딩) 까지만 저장. "
                        "채널을 빠르게 비우고 싶을 때 (1메시지 ~2-3초). "
                        "그 후 `python -m backend.jobs.backfill_summary` 로 일괄 summary 생성.")
    args = p.parse_args()

    backfill_count = 0 if args.no_backfill else args.backfill

    agent = TelegramChannelAgent(analyze_now=not args.fast)
    rc = asyncio.run(agent.run(backfill=backfill_count, listen=not args.no_listen))
    sys.exit(rc)
