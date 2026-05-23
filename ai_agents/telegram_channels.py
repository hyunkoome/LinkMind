"""
ai_agents/telegram_channels.py
----------------------------------------------------------------------------
config/telegram_channels.yaml 로드 + 검증. multi-channel watcher 가 사용.

yaml 스키마:

    batch_size: 5                                # 선택 (default 5) — batch sequential 단위
    channels:
      - invite: https://t.me/+abc                # 필수
        delete_after_ingest: true                # 필수 (채널별 inbox 패턴 on/off)
        name: LinkMind-Inbox                     # 선택 (로그/식별용, 비우면 채널 title 자동)

비밀이 아닌 운영 데이터라 git commit 가능. env 에는 path 만 (TELEGRAM_CHANNELS_CONFIG).
yaml 파일 미존재 시 빈 TelegramWatcherConfig (default 값) — watcher 가 single fallback.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("telegram-channels")

# batch_size 의 기본값 + 안전 한계 — 너무 크면 Telegram FloodWait 위험.
_DEFAULT_BATCH_SIZE = 5
_MAX_BATCH_SIZE = 20


@dataclass(frozen=True)
class TelegramChannelConfig:
    """yaml 의 한 채널 entry. invite + delete_after_ingest 는 필수, name 은 선택."""

    invite: str
    delete_after_ingest: bool
    name: str | None = None

    def display_name(self) -> str:
        """로그 표시용 — name 우선, 없으면 invite 의 짧은 형태."""
        if self.name:
            return self.name
        # invite link 의 hash 부분 또는 채널명 짧게.
        return self.invite.rsplit("/", 1)[-1][:30]


@dataclass(frozen=True)
class TelegramWatcherConfig:
    """yaml 전체 — batch_size + 채널 list. watcher 가 이 namespace 받아 운영."""

    batch_size: int = _DEFAULT_BATCH_SIZE
    channels: list[TelegramChannelConfig] = field(default_factory=list)


def load_telegram_channels(yaml_path: str | Path) -> TelegramWatcherConfig:
    """yaml 파일에서 채널 list + batch_size 로드 + 검증.

    Args:
        yaml_path: yaml 파일 경로 (절대/상대 모두 OK).

    Returns:
        TelegramWatcherConfig — 파일 미존재 시 default (빈 channels + batch_size=5).

    Raises:
        RuntimeError: yaml 파싱 실패 / 필수 필드 누락 / 타입 불일치 / batch_size 범위.
    """
    path = Path(yaml_path)
    if not path.exists():
        logger.info("telegram channels yaml 미존재 (%s) — single fallback 만 사용", path)
        return TelegramWatcherConfig()

    try:
        import yaml  # 지연 import — PyYAML 미설치 환경에서도 module import 자체는 OK.
    except ImportError as e:  # noqa: BLE001
        raise RuntimeError(
            "PyYAML 미설치 — pip install PyYAML 또는 requirements.txt 재설치"
        ) from e

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:  # type: ignore[attr-defined]
        raise RuntimeError(f"yaml 파싱 실패 ({path}): {e}") from e

    if raw is None:
        # 빈 yaml 파일
        return TelegramWatcherConfig()
    if not isinstance(raw, dict) or "channels" not in raw:
        raise RuntimeError(
            f"yaml 최상위에 'channels' key 가 없음 ({path}). 스키마는 docstring 참조."
        )

    # batch_size — 선택 필드, default 5. 1 이상 _MAX_BATCH_SIZE 이하.
    batch_size = _DEFAULT_BATCH_SIZE
    if "batch_size" in raw:
        bs = raw["batch_size"]
        if not isinstance(bs, int) or isinstance(bs, bool):
            raise RuntimeError(
                f"batch_size 는 정수여야 함 ({path}, 실제: {type(bs).__name__})"
            )
        if bs < 1 or bs > _MAX_BATCH_SIZE:
            raise RuntimeError(
                f"batch_size 는 1~{_MAX_BATCH_SIZE} 사이 ({path}, 실제: {bs}). "
                f"너무 크면 Telegram FloodWait 위험."
            )
        batch_size = bs

    items = raw["channels"]
    if not isinstance(items, list):
        raise RuntimeError(f"'channels' 는 list 여야 함 ({path}, 실제: {type(items).__name__})")

    seen: set[str] = set()
    out: list[TelegramChannelConfig] = []
    for idx, entry in enumerate(items):
        if not isinstance(entry, dict):
            raise RuntimeError(
                f"channels[{idx}] 가 dict 아님 ({path}, 실제: {type(entry).__name__})"
            )
        invite = entry.get("invite")
        if not invite or not isinstance(invite, str):
            raise RuntimeError(
                f"channels[{idx}].invite 가 비어있거나 문자열 아님 ({path})"
            )
        if "delete_after_ingest" not in entry:
            raise RuntimeError(
                f"channels[{idx}].delete_after_ingest 누락 — 필수 필드 ({path}, invite={invite})"
            )
        delete_flag = entry["delete_after_ingest"]
        if not isinstance(delete_flag, bool):
            raise RuntimeError(
                f"channels[{idx}].delete_after_ingest 가 bool 아님 ({path}, 실제: {type(delete_flag).__name__})"
            )
        name = entry.get("name")
        if name is not None and not isinstance(name, str):
            raise RuntimeError(
                f"channels[{idx}].name 이 문자열 아님 ({path}, 실제: {type(name).__name__})"
            )

        invite = invite.strip()
        if invite in seen:
            logger.warning("yaml 의 중복 invite skip: %s", invite)
            continue
        seen.add(invite)
        out.append(TelegramChannelConfig(
            invite=invite,
            delete_after_ingest=delete_flag,
            name=name.strip() if name else None,
        ))
    return TelegramWatcherConfig(batch_size=batch_size, channels=out)
