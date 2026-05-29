"""
backend.ingest.url.auto_link_topics 단위 테스트 — DB 없이 mock session 으로.

repository 의 find_or_create_topic / link_item_to_topic 를 monkeypatch 해서
호출 순서와 인자를 기록 → auto_link_topics 의 결정 로직만 단위로 검증.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from backend.ingest import url as url_module
from backend.utils.external_ids import ExternalId


class _State:
    def __init__(self) -> None:
        self.topics: dict[str, dict] = {}              # slug → topic dict
        self.links: list[dict] = []                    # link 호출 기록

    async def find_or_create(self, session, *, slug, title, primary_external_id):
        if slug in self.topics:
            return self.topics[slug], False
        t = {
            "id": uuid4(),
            "slug": slug,
            "title": title,
            "primary_external_id": primary_external_id,
        }
        self.topics[slug] = t
        return t, True

    async def link(
        self, session, *, item_id, topic_id, role, confidence=1.0, source="auto", note=None,
    ):
        self.links.append({
            "item_id": item_id,
            "topic_id": topic_id,
            "role": role,
            "confidence": confidence,
            "source": source,
        })
        return True


@pytest.fixture
def state(monkeypatch) -> _State:
    s = _State()
    monkeypatch.setattr(url_module, "find_or_create_topic", s.find_or_create)
    monkeypatch.setattr(url_module, "link_item_to_topic", s.link)
    return s


@pytest.mark.asyncio
async def test_pdf_with_arxiv_and_github_creates_two_topics(state: _State):
    """PDF 본문에서 arxiv_id + github_repo 둘 다 발견되면 두 topic 모두 link.

    arxiv 가 primary (priority 0) → confidence 1.0. github 는 cross-modal 단서 → 0.7.
    role 은 'item 의 modality' 이므로 둘 다 'pdf' (이 item 이 PDF 이기 때문).
    """
    item_id = uuid4()
    ids = [
        ExternalId(kind="arxiv", value="2106.09685"),
        ExternalId(kind="github", value="microsoft/LoRA"),
    ]
    matched = await url_module.auto_link_topics(
        session=None,
        item_id=item_id,
        source_type="pdf",
        title="LoRA Paper",
        ids=ids,
    )

    assert {t["slug"] for t in matched} == {"arxiv:2106.09685", "github:microsoft/LoRA"}

    # arxiv topic 이 1.0 (main), github 는 0.7 (보조)
    by_slug = {t["slug"]: t for t in matched}
    assert by_slug["arxiv:2106.09685"]["confidence"] == 1.0
    assert by_slug["github:microsoft/LoRA"]["confidence"] == 0.7

    # role 은 item 의 modality — 같은 PDF item 이므로 두 link 모두 'pdf'.
    # (어느 topic 의 어느 다른 item 이 'code' modality 인지는 그 item 들의 link 에서 결정)
    assert by_slug["arxiv:2106.09685"]["role"] == "pdf"
    assert by_slug["github:microsoft/LoRA"]["role"] == "pdf"

    # 호출 횟수: 두 topic 모두 link
    assert len(state.links) == 2


@pytest.mark.asyncio
async def test_two_items_same_arxiv_share_topic(state: _State):
    """다른 item 두 개가 같은 arxiv_id 를 가지면 같은 topic 으로 link → 자동 그룹핑.

    url-type 의 정체성은 자기 URL 에서 (D10.6 A2) — arxiv abstract URL 을 넘기면
    arxiv 가 정체성. pdf 는 native kind 가 arxiv 라 ids 만으로 arxiv 정체성.
    """
    item_a = uuid4()
    item_b = uuid4()
    ids = [ExternalId(kind="arxiv", value="2106.09685")]

    await url_module.auto_link_topics(
        session=None, item_id=item_a, source_type="url", title="LoRA abs", ids=ids,
        url="https://arxiv.org/abs/2106.09685",
    )
    await url_module.auto_link_topics(
        session=None, item_id=item_b, source_type="pdf", title="LoRA paper PDF", ids=ids,
    )

    # 두 link 가 같은 topic_id 를 가리켜야
    topic_ids = {link["topic_id"] for link in state.links}
    assert len(topic_ids) == 1

    # url + arxiv → role='paper', pdf + arxiv → role='pdf'
    roles = sorted(link["role"] for link in state.links)
    assert roles == ["paper", "pdf"]


@pytest.mark.asyncio
async def test_youtube_video_creates_video_topic(state: _State):
    item_id = uuid4()
    ids = [ExternalId(kind="yt", value="PYr-LSOf2OY")]
    matched = await url_module.auto_link_topics(
        session=None, item_id=item_id, source_type="youtube",
        title="Demo Video", ids=ids,
    )
    assert len(matched) == 1
    assert matched[0]["slug"] == "yt:PYr-LSOf2OY"
    assert matched[0]["role"] == "video"


@pytest.mark.asyncio
async def test_empty_external_ids_creates_fallback_topic(state: _State):
    """external_id 가 하나도 없는 url 도 자체 fallback topic 1개 생성 (wave-3, 2026-05-18).

    이유: 그래프에서 모든 자료가 일급 시민이 되도록. slug=`url:item:<uuid>` 형식.
    카테고리 link 도 가능해짐 (items.tags → topic.tags 자동 매칭).
    """
    item_id = uuid4()
    out = await url_module.auto_link_topics(
        session=None, item_id=item_id, source_type="url",
        title="My Blog Post", ids=[],
    )
    assert len(out) == 1
    fallback = out[0]
    assert fallback["slug"] == f"url:item:{item_id}"
    assert fallback["title"] == "My Blog Post"
    assert fallback["primary_external_id"] is None
    assert fallback["role"] == "primary"
    assert fallback["confidence"] == 1.0
    # link 도 1회 호출됐어야 함 (item ↔ fallback topic)
    assert len(state.links) == 1
    assert state.links[0]["topic_id"] == fallback["id"]
    assert state.links[0]["item_id"] == item_id
    assert state.links[0]["role"] == "primary"


@pytest.mark.asyncio
async def test_empty_external_ids_no_title_uses_slug_as_title(state: _State):
    """title 도 없으면 fallback topic 의 title 은 slug 자체."""
    item_id = uuid4()
    out = await url_module.auto_link_topics(
        session=None, item_id=item_id, source_type="url",
        title=None, ids=[],
    )
    assert len(out) == 1
    assert out[0]["title"] == f"url:item:{item_id}"


@pytest.mark.asyncio
async def test_github_repo_with_arxiv_paper_link_is_github_identity(state: _State):
    """GitHub repo item 의 ext_ids 가 self (github) + README 의 arxiv 면 github 가 정체성.

    D10.6 A2 (2026-05-29): '1 링크 = 1 위키' — 자료의 정체성은 자기 타입(github).
    README 가 인용한 arxiv paper 는 0.7 관계 clue 로만 (별도 정체성 X). 옛 동작은
    arxiv 를 primary 로 둬서 github repo 가 논문 wiki 에 흡수되는 중복의 원인이었다.
    """
    item_id = uuid4()
    ids = [
        ExternalId(kind="github", value="microsoft/LoRA"),
        ExternalId(kind="arxiv", value="2106.09685"),
    ]
    matched = await url_module.auto_link_topics(
        session=None, item_id=item_id, source_type="github",
        title="microsoft/LoRA", ids=ids,
    )

    by_slug = {t["slug"]: t for t in matched}
    # github 가 정체성 (1.0) — 이 자료는 github repo 그 자체
    assert by_slug["github:microsoft/LoRA"]["confidence"] == 1.0
    assert by_slug["github:microsoft/LoRA"]["role"] == "code"
    # README 가 인용한 arxiv 는 0.7 관계 clue (별도 정체성 wiki 로 승격 X)
    assert by_slug["arxiv:2106.09685"]["confidence"] == 0.7
    assert by_slug["arxiv:2106.09685"]["role"] == "code"
