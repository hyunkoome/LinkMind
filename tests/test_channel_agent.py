"""ai_agents.base.ChannelAgent ABC 의 동작 단위 테스트.

Phase 2.5 — multi-channel gateway 추상화의 첫 번째 layer. ABC 가 abstract method
강제와 공통 헬퍼 두 가지를 잘 하는지 회귀 방지.
"""

from __future__ import annotations

import pytest

from ai_agents.base import ChannelAgent


def test_cannot_instantiate_bare_abstract_base():
    """ChannelAgent 자체는 abstract — 직접 인스턴스화 시 TypeError."""
    with pytest.raises(TypeError):
        ChannelAgent()  # type: ignore[abstract]


def test_subclass_missing_run_is_still_abstract():
    """setup 만 구현하고 run 안 한 subclass 도 abstract — 인스턴스화 시 TypeError."""

    class Half(ChannelAgent):
        name = "half"

        async def setup(self) -> None:
            return None

    with pytest.raises(TypeError):
        Half()  # type: ignore[abstract]


def test_subclass_missing_setup_is_still_abstract():
    """run 만 구현하고 setup 안 한 subclass 도 abstract."""

    class Halfb(ChannelAgent):
        name = "halfb"

        async def run(self, *, backfill: int = 0, listen: bool = True) -> int:
            return 0

    with pytest.raises(TypeError):
        Halfb()  # type: ignore[abstract]


def test_full_subclass_instantiates_ok():
    """setup + run 둘 다 구현하면 인스턴스화 가능."""

    class Full(ChannelAgent):
        name = "full"

        async def setup(self) -> None:
            return None

        async def run(self, *, backfill: int = 0, listen: bool = True) -> int:
            return 0

    agent = Full()
    assert agent.name == "full"
    assert isinstance(agent, ChannelAgent)


def test_is_ingest_successful_is_staticmethod_callable_without_instance():
    """is_ingest_successful 은 staticmethod — 인스턴스 없이 직접 호출 가능."""
    # subclass 정의/인스턴스화 없이 ABC 의 staticmethod 만 써도 동작
    assert ChannelAgent.is_ingest_successful({"urls_ingested": [{"url": "x"}]}) is True
    assert ChannelAgent.is_ingest_successful({"urls_ingested": [], "note_item_id": "n"}) is True
    assert ChannelAgent.is_ingest_successful({}) is False


# ── attachments 케이스 (Phase 2.5 wave-3 확장) ──────────────


def test_is_ingest_successful_attachments_all_ok():
    """첨부 모두 error 없이 처리 → True (메시지 삭제 가능)."""
    result = {
        "urls_ingested": [],
        "attachments_ingested": [
            {"filename": "a.pdf", "item_id": "..."},
            {"filename": "b.docx", "item_id": "..."},
        ],
    }
    assert ChannelAgent.is_ingest_successful(result) is True


def test_is_ingest_successful_attachment_with_error_false():
    """첨부 하나라도 error 면 False — 메시지 보존 (사용자가 발견)."""
    result = {
        "urls_ingested": [],
        "attachments_ingested": [
            {"filename": "a.pdf", "item_id": "..."},
            {"filename": "b.docx", "error": "extraction failed"},
        ],
    }
    assert ChannelAgent.is_ingest_successful(result) is False


def test_is_ingest_successful_mixed_urls_and_attachments():
    """URL + 첨부 + note 다 있는 경우 — 전부 성공해야 True."""
    result = {
        "urls_ingested": [{"url": "https://x.com", "item_id": "u1"}],
        "attachments_ingested": [{"filename": "a.pdf", "item_id": "a1"}],
        "note_item_id": "n1",
    }
    assert ChannelAgent.is_ingest_successful(result) is True


def test_is_ingest_successful_url_ok_but_attachment_fail_false():
    """URL 다 OK 지만 첨부 하나 실패 → False (메시지 보존)."""
    result = {
        "urls_ingested": [{"url": "https://x.com", "item_id": "u1"}],
        "attachments_ingested": [{"filename": "a.pdf", "error": "download failed"}],
    }
    assert ChannelAgent.is_ingest_successful(result) is False


def test_is_ingest_successful_attachment_fail_url_ok_false():
    """대칭 케이스 — 첨부 OK + URL 실패 → False."""
    result = {
        "urls_ingested": [{"url": "https://x.com", "error": "404"}],
        "attachments_ingested": [{"filename": "a.pdf", "item_id": "a1"}],
    }
    assert ChannelAgent.is_ingest_successful(result) is False


def test_default_name_is_empty_string():
    """ABC 의 name 기본값은 빈 문자열 — 구현체가 override 강제 안 함 (warning 도 X).

    의도: name 안 정해도 인스턴스화는 되되, 채널 로깅 시 사람이 알아보기 어려우므로
    구현체가 명시적으로 정하는 게 best practice (예: `name = "telegram"`).
    """

    class Anon(ChannelAgent):
        # name override 안 함 — 기본값 "" 그대로
        async def setup(self) -> None:
            return None

        async def run(self, *, backfill: int = 0, listen: bool = True) -> int:
            return 0

    agent = Anon()
    assert agent.name == ""
