"""ai_agents — LinkMind 의 multi-channel gateway 모듈.

각 채널 (telegram / slack / whatsapp / discord) 의 inbox watcher daemon 이 모임.
모두 backend HTTP API (또는 backend.ingest.* 모듈 직접 import) 호출 — backend
LLMProvider 직접 호출 X (CLAUDE.md §11 + §3 책임 분리).

자세한 아키텍처는 docs/agent_architecture.md.
"""

from __future__ import annotations

from ai_agents.base import ChannelAgent

__all__ = ["ChannelAgent"]
