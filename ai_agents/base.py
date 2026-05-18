"""ChannelAgent ABC — multi-channel gateway 추상 base.

LinkMind 의 `ai_agents/` 디렉토리 안의 모든 채널 watcher (telegram / slack /
whatsapp / discord 등) 가 이 ABC 를 상속한다. 가벼운 추상화로 의도적으로 강제는
적게 — 채널마다 메시지 stream 패턴 (callback / iterator / polling / webhook) 이
달라서 일률화하기 어려움.

설계 결정:
- `setup()` / `run()` 두 abstract method 만 — 진입점 표준화
- 공통 헬퍼 `is_ingest_successful()` — backend.ingest.* 결과 dict 의 성공 판정
  로직을 한 곳에 모아 채널 간 일관성 보장 (inbox 패턴의 자동 메시지 삭제 트리거)
- ChannelMessage 같은 cross-channel dataclass 는 의도적으로 정의하지 않음 — 첫
  번째 추가 채널 (Phase 3+ slack) 도입할 때 진짜 필요한지 보고 결정 (§6 MVP
  원칙: 미래 가정 추상화 금지)

책임 분리 (CLAUDE.md §11):
- ✅ backend.ingest.* 모듈 직접 import 호출 (같은 프로세스, HTTP overhead 피함)
- ❌ backend.llm.* LLMProvider 직접 호출 — 필요하면 backend HTTP `/ask` 경유

자세히는 docs/agent_architecture.md §3.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar


class ChannelAgent(ABC):
    """multi-channel gateway 추상 base.

    구현체 예시:
        class TelegramChannelAgent(ChannelAgent):
            name = "telegram"

            async def setup(self) -> None:
                self.client = TelegramClient(...)
                await self.client.start()

            async def run(self, *, backfill: int = 0, listen: bool = True) -> int:
                if backfill > 0:
                    await self._backfill(backfill)
                if listen:
                    await self.client.run_until_disconnected()
                return 0
    """

    #: 채널 식별자 ("telegram" / "slack" / "discord" / "whatsapp"). 구현체가 override.
    name: ClassVar[str] = ""

    @abstractmethod
    async def setup(self) -> None:
        """auth, session 복구, 채널 join 등 초기화.

        실패 시 RuntimeError 발생 — `run()` 호출 전에 명시적으로 부르거나
        구현체의 `run()` 초입에서 호출.
        """

    @abstractmethod
    async def run(self, *, backfill: int = 0, listen: bool = True) -> int:
        """daemon 진입점.

        Args:
            backfill: 채널의 최근 N개 메시지를 먼저 처리 (0 = skip).
            listen:   처리 후 실시간 stream 계속 listen (False = backfill 만 하고 종료).

        Returns:
            exit code (0 = 정상 종료).

        구현체가 listen/polling/webhook 어느 패턴이든 자유. 단 backfill 과
        listen 의 의미는 위 정의 유지.
        """

    @staticmethod
    def is_ingest_successful(result: dict[str, Any]) -> bool:
        """backend.ingest.* 의 결과 dict 가 의미있게 끝났는지 판단.

        inbox 패턴의 "성공한 메시지는 채널에서 자동 삭제" 트리거 조건.

        규칙 (Phase 2.5 wave-3 확장 — attachments 도 포함):
        - 결과에 있는 모든 ingest 시도 (urls + attachments) 가 error 없음
        - + 최소 하나는 처리됨 (urls / attachments / note 중 하나 이상)
        - 그렇지 않으면 False — 채널 측이 메시지 보존 (사용자가 시각으로 확인).

        Args:
            result: backend.ingest.* 의 반환 dict. 예:
                ```
                {
                    "msg_id": "...",
                    "urls_ingested": [{"url": "...", "item_id": "..."}, ...],
                    "attachments_ingested": [{"filename": "...", "item_id": "..."}, ...],
                    "note_item_id": "abc-uuid" | None,
                }
                ```

        Returns:
            True 면 채널 측이 메시지 삭제 가능 (이미 안전하게 저장됨).
        """
        urls = result.get("urls_ingested") or []
        attachments = result.get("attachments_ingested") or []
        note = result.get("note_item_id")

        has_any = bool(urls or attachments or note)
        if not has_any:
            return False

        urls_ok = all("error" not in u for u in urls)
        attachments_ok = all("error" not in a for a in attachments)
        return urls_ok and attachments_ok
