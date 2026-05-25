#!/usr/bin/env python
"""
ai_agents/check_telegram_invites.py
----------------------------------------------------------------------------
config/telegram_channels.yaml 의 invite link 들이 살아있는지 일괄 검증.
선택적으로 죽은 invite 를 yaml + channel_id_cache.json 에서 자동 정리.

사용 시점:
1. 사용자가 텔레그램에서 채널을 leave / 추방 / 삭제했을 때 — yaml 에서 정리할
   대상 식별 + (옵션) 자동 정리.
2. watcher 가 ChannelPrivateError 로 죽은 채널을 무한 retry 하기 시작하면 — 어느
   channel_id 가 stale 한지 확인.

분류:
  ALIVE         사용자가 멤버이고 dialog 에 존재 — watcher 가 listen/backfill 가능
  NOT-MEMBER    invite link 는 valid 하지만 사용자가 채널 멤버 아님 — leave/추방됨
  DEAD-HASH    invite 자체가 invalid/expired (revoke 됨) — 채널 owner 가 link 무효화

exit code: 0 (all ALIVE) / 1 (NOT-MEMBER 또는 DEAD-HASH 있음, 또는 apply 실행).

⚠️ Telethon session 은 process 1개만 잡을 수 있음. watcher 가 떠있으면 OperationalError
나니 먼저 `bash scripts/step5_run_dev.sh --stop` 으로 watcher 정지 후 실행.

사용:
    python -m ai_agents.check_telegram_invites                    # dry-run (권장만 print)
    python -m ai_agents.check_telegram_invites --apply            # yaml + cache 자동 정리
    python -m ai_agents.check_telegram_invites --yaml-path config/telegram_channels.yaml
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

# project root 를 sys.path 에 — `python ai_agents/check_telegram_invites.py` 도 동작.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from ai_agents.telegram_channels import (  # noqa: E402
    TelegramChannelConfig,
    load_telegram_channels,
)
from ai_agents.telegram_inbox_watcher import _invite_hash  # noqa: E402
from backend.config import get_settings  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
logger = logging.getLogger("check-invites")

# 한 invite 검증 사이 sleep — Telegram rate limit 예방.
_CHECK_SLEEP_S = 2.0

# yaml entry 의 시작 라인 패턴 — `  - invite: <url>` (들여쓰기 2칸 + dash).
# 다음 entry 시작 (또는 top-level key) 까지가 한 entry 의 라인 범위.
_INVITE_LINE_RE = re.compile(r"^\s*-\s*invite:\s*(\S+)\s*$")
_NEXT_ENTRY_RE = re.compile(r"^\s*-\s")           # 다음 list entry 의 시작
_TOP_LEVEL_RE = re.compile(r"^[^\s#]")             # 들여쓰기 없는 top-level key


def remove_invites_from_yaml(yaml_text: str, invites_to_remove: set[str]) -> tuple[str, int]:
    """yaml 텍스트에서 지정된 invite URL 의 entry 블록을 라인 단위로 제거.

    PyYAML 로 load → dump 하면 주석/포맷/순서 다 깨짐. line-based 가 raw 보존 OK.

    한 entry = `  - invite: <url>` 라인 + 다음 entry/top-level 이전까지의 모든 라인
    (delete_after_ingest, name 등). 빈 라인이나 주석도 함께 (해당 entry 뒤에 붙은
    것으로 간주). 단 빈 라인이 두 entry 사이의 시각적 구분이면 그건 다음 entry
    의 prefix 라 그 시점에서 멈춤.

    Args:
        yaml_text: 원본 yaml 텍스트 (newline 보존).
        invites_to_remove: 제거할 invite URL 집합 (정확 매치).

    Returns:
        (정리된 yaml_text, 실제 제거된 entry 수).
    """
    if not invites_to_remove:
        return yaml_text, 0

    lines = yaml_text.splitlines(keepends=True)
    out: list[str] = []
    removed = 0
    i = 0
    while i < len(lines):
        line = lines[i]
        m = _INVITE_LINE_RE.match(line)
        if m and m.group(1).strip() in invites_to_remove:
            # 이 entry 의 시작 — 다음 entry/top-level 까지 모두 skip.
            i += 1
            while i < len(lines):
                nxt = lines[i]
                # 다음 entry start (들여쓴 dash) 또는 top-level (들여쓰기 없는 키) 이면 중단.
                # 빈 줄은 다음 entry 의 시각적 분리자로 간주 — skip 멈춤 (현재 entry 끝).
                if _NEXT_ENTRY_RE.match(nxt) or _TOP_LEVEL_RE.match(nxt):
                    break
                if nxt.strip() == "":
                    # 빈 줄은 다음 entry 의 prefix — skip 멈춤 (출력 안 함, 다음 entry 가
                    # 알아서 들고 옴). 그러나 마지막 entry 였다면 EOF 까지 빈 줄 skip.
                    break
                i += 1
            removed += 1
            continue
        out.append(line)
        i += 1
    return "".join(out), removed


def remove_hashes_from_cache(cache: dict[str, int], hashes_to_remove: set[str]) -> tuple[dict[str, int], int]:
    """cache dict 에서 지정된 invite hash entry 제거. (새 dict, 제거 수) 반환."""
    if not hashes_to_remove:
        return dict(cache), 0
    out = {k: v for k, v in cache.items() if k not in hashes_to_remove}
    return out, len(cache) - len(out)


def _backup_file(path: Path) -> Path:
    """파일을 .bak.<timestamp> 로 백업. 백업 경로 반환. 파일 없으면 None."""
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = path.with_suffix(path.suffix + f".bak.{ts}")
    shutil.copy2(path, bak)
    return bak


def apply_cleanup(
    yaml_path: Path,
    cache_path: Path,
    invites_to_remove: list[str],
) -> tuple[int, int]:
    """yaml + cache 둘 다 백업 후 정리. (yaml 제거 수, cache 제거 수) 반환.

    invites_to_remove 의 각 URL 에서 hash 추출해 cache key 로 사용.
    """
    invite_set = set(invites_to_remove)
    hashes = {h for h in (_invite_hash(u) for u in invites_to_remove) if h}

    # yaml 정리
    yaml_text = yaml_path.read_text(encoding="utf-8")
    new_yaml, yaml_removed = remove_invites_from_yaml(yaml_text, invite_set)
    if yaml_removed:
        yaml_bak = _backup_file(yaml_path)
        yaml_path.write_text(new_yaml, encoding="utf-8")
        logger.info("yaml 정리: %d entry 제거 (백업: %s)", yaml_removed, yaml_bak.name)
    else:
        logger.info("yaml: 제거 대상 entry 발견 못함 (이미 정리된 듯)")

    # cache 정리 (파일 없으면 skip)
    cache_removed = 0
    if cache_path.exists():
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
        new_cache, cache_removed = remove_hashes_from_cache(cache, hashes)
        if cache_removed:
            cache_bak = _backup_file(cache_path)
            cache_path.write_text(
                json.dumps(new_cache, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            logger.info(
                "cache 정리: %d entry 제거 (백업: %s)", cache_removed, cache_bak.name,
            )
        else:
            logger.info("cache: 제거 대상 hash 발견 못함")
    else:
        logger.info("cache 파일 없음 (%s) — skip", cache_path)

    return yaml_removed, cache_removed


async def check_all(yaml_path: Path, *, apply: bool = True) -> int:
    """yaml 의 모든 invite 검증 + (기본) yaml/cache 자동 정리.

    Args:
        yaml_path: 검증할 yaml.
        apply: True (기본) 면 NOT-MEMBER/DEAD-HASH 발견 시 yaml + cache 자동 정리 (백업 후).
               False (--dry-run) 면 권장만 print, 파일 건드림 X.

    Returns:
        exit code — 0 (all ALIVE 또는 apply 성공) / 1 (dry-run 에서 정리 대상 있음 또는 에러).
    """
    settings = get_settings()
    cfg = load_telegram_channels(yaml_path)
    if not cfg.channels:
        logger.error("yaml 의 channels 가 비어있음 — 검증할 게 없음 (%s)", yaml_path)
        return 1

    # Telethon import 는 지연 — 다른 환경에서도 module import 자체는 동작.
    try:
        from telethon import TelegramClient
        from telethon.errors import (
            FloodWaitError,
            InviteHashEmptyError,
            InviteHashExpiredError,
            InviteHashInvalidError,
        )
        from telethon.tl.functions.messages import CheckChatInviteRequest
        from telethon.tl.types import ChatInvite, ChatInviteAlready
    except ImportError as e:  # noqa: BLE001
        logger.error("Telethon 미설치 — pip install telethon (%s)", e)
        return 1

    if not settings.telegram_api_id or not settings.telegram_api_hash:
        logger.error("TELEGRAM_API_ID / TELEGRAM_API_HASH 미설정 (env/dev.env)")
        return 1

    session_path = Path(settings.telegram_session_path)
    try:
        api_id_int = int(settings.telegram_api_id)
    except ValueError:
        logger.error("TELEGRAM_API_ID 가 정수 아님: %r", settings.telegram_api_id)
        return 1

    client = TelegramClient(str(session_path), api_id_int, settings.telegram_api_hash)
    try:
        await client.start()
    except Exception as e:  # noqa: BLE001
        # 가장 흔한 원인 — watcher daemon 이 같은 session 잡고 있음.
        logger.error(
            "Telethon start 실패: %s — watcher daemon 이 켜져있으면 'bash "
            "scripts/step5_run_dev.sh --stop' 으로 먼저 정지", e,
        )
        return 1

    # dialog snapshot — 사용자가 멤버인 채널 source of truth.
    logger.info("dialog snapshot 수집 중…")
    my_dialogs: dict[int, str] = {}
    async for dlg in client.iter_dialogs(limit=500):
        eid = getattr(dlg.entity, "id", None)
        if eid:
            my_dialogs[int(eid)] = dlg.name
    logger.info("내가 멤버인 채널/그룹/유저: %d 개", len(my_dialogs))

    alive: list[tuple[int, TelegramChannelConfig, str]] = []
    not_member: list[tuple[int, TelegramChannelConfig, str]] = []
    dead: list[tuple[int, TelegramChannelConfig, str]] = []
    skipped: list[tuple[int, TelegramChannelConfig, str]] = []

    print()
    print(f"{'#':>3}  {'invite':45s}  {'status':12s}  title")
    print("-" * 115)

    for idx, ch in enumerate(cfg.channels, start=1):
        h = _invite_hash(ch.invite)
        if h is None:
            print(f"{idx:3d}  {ch.invite:45s}  SKIP-NON-LINK  (채널명/ID 형식 — 수동 확인)")
            skipped.append((idx, ch, "non-link"))
            continue

        if idx > 1:
            await asyncio.sleep(_CHECK_SLEEP_S)

        try:
            result = await client(CheckChatInviteRequest(h))
            if isinstance(result, ChatInviteAlready):
                chat = result.chat
                title = getattr(chat, "title", "?")
                cid = int(getattr(chat, "id", 0))
                left = bool(getattr(chat, "left", False))
                kicked = bool(getattr(chat, "kicked", False))
                in_dialog = cid in my_dialogs
                if left or kicked or not in_dialog:
                    reason = (
                        f"left={left}, kicked={kicked}, in_dialog={in_dialog}"
                    )
                    print(f"{idx:3d}  {ch.invite:45s}  ❌ NOT-MEMBER    '{title}' ({reason})")
                    not_member.append((idx, ch, title))
                else:
                    print(f"{idx:3d}  {ch.invite:45s}  ✅ ALIVE         '{title}' (id={cid})")
                    alive.append((idx, ch, title))
            elif isinstance(result, ChatInvite):
                title = getattr(result, "title", "?")
                participants = getattr(result, "participants_count", 0)
                print(
                    f"{idx:3d}  {ch.invite:45s}  ❌ NOT-MEMBER    "
                    f"'{title}' (참여자 {participants}명, 사용자는 멤버 아님)"
                )
                not_member.append((idx, ch, title))
            else:
                # 예상 못한 응답 타입 — Telethon 향후 버전 호환.
                print(f"{idx:3d}  {ch.invite:45s}  ⚠️ UNKNOWN-RESP  {type(result).__name__}")
                skipped.append((idx, ch, type(result).__name__))
        except (InviteHashExpiredError, InviteHashInvalidError, InviteHashEmptyError) as e:
            err = type(e).__name__
            print(f"{idx:3d}  {ch.invite:45s}  ❌ DEAD-HASH     [{err}] (invite revoke 됨)")
            dead.append((idx, ch, err))
        except FloodWaitError as e:
            print(f"{idx:3d}  {ch.invite:45s}  ⏳ FLOOD-WAIT    {e.seconds}s 대기 필요")
            skipped.append((idx, ch, f"FloodWait {e.seconds}s"))
            # 짧으면 잠시 기다리고 계속, 너무 길면 그냥 다음으로.
            if e.seconds <= 60:
                await asyncio.sleep(e.seconds + 1)
        except Exception as e:  # noqa: BLE001
            print(f"{idx:3d}  {ch.invite:45s}  ⚠️ ERROR         {type(e).__name__}: {e}")
            skipped.append((idx, ch, type(e).__name__))

    await client.disconnect()

    # 요약
    print()
    print("=" * 60)
    print(
        f"결과: ALIVE={len(alive)}  NOT-MEMBER={len(not_member)}  "
        f"DEAD-HASH={len(dead)}  SKIPPED={len(skipped)}  /  총 {len(cfg.channels)}"
    )
    bad = not_member + dead
    if bad:
        print()
        print("정리 대상:")
        for idx, ch, info in bad:
            print(f"  [{idx:2d}] {ch.invite}   ({info})")
        print()
        if apply:
            # 디폴트 흐름 — 검증 즉시 yaml + cache 자동 정리. 백업 만든 후 in-place.
            settings = get_settings()
            cache_path = Path(settings.telegram_session_path).parent / "channel_id_cache.json"
            if not cache_path.is_absolute():
                cache_path = _ROOT / cache_path
            invites = [ch.invite for _, ch, _ in bad]
            yaml_n, cache_n = apply_cleanup(yaml_path, cache_path, invites)
            print()
            print(f"✅ 자동 정리 완료 — yaml {yaml_n} entry / cache {cache_n} hash 제거 (.bak 백업 생성)")
            print("=" * 60)
            # 정리 성공 = 후속 process 가 정상 진행 가능 → exit 0
            return 0
        else:
            print(
                "→ --dry-run 모드라 파일 안 건드림. 정리하려면 옵션 없이 다시 실행."
            )
            print("=" * 60)
            return 1
    print("=" * 60)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="config/telegram_channels.yaml 의 invite 일괄 검증 + 자동 정리.",
    )
    parser.add_argument(
        "--yaml-path",
        type=Path,
        default=None,
        help="yaml 경로 (기본: env/Settings 의 telegram_channels_config — "
             "보통 config/telegram_channels.yaml).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="검증만 하고 파일 변경 안 함 (기본은 자동 정리 + 백업).",
    )
    args = parser.parse_args()
    yaml_path = args.yaml_path or Path(get_settings().telegram_channels_config)
    if not yaml_path.is_absolute():
        yaml_path = _ROOT / yaml_path
    if not yaml_path.exists():
        logger.error("yaml 파일 없음: %s", yaml_path)
        return 1
    return asyncio.run(check_all(yaml_path, apply=not args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
