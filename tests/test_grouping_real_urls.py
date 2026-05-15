"""
실제 URL 데이터로 그룹핑 흐름 통합 검증.

URL/기대값은 `tests/resources/test_urls.json` — 새 케이스 추가 시 그 파일만 갱신.
DB/네트워크 접속 없이 extract_external_ids + auto_link_topics (mock session) 만 사용.

검증 두 단계:
1. URL 자체에서 추출되는 ext_ids 가 fixture 의 expected 와 일치.
2. 한 group 안의 모든 item 이 같은 cross-modal 단서를 가지면 같은 topic 으로 묶임.
   다른 group 의 item 은 절대 같은 topic 에 안 묶임.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from backend.ingest import url as url_module
from backend.utils.external_ids import (
    ExternalId,
    extract_external_ids,
    primary_external_id,
)


URLS_JSON = Path(__file__).parent / "resources" / "test_urls.json"


def _load_groups() -> list[dict]:
    return json.loads(URLS_JSON.read_text())["groups"]


@pytest.fixture(scope="module")
def groups() -> list[dict]:
    return _load_groups()


# ── 1단계: URL → ext_ids 추출이 fixture 의 expected 와 맞는지 ──


def test_each_url_extracts_expected_external_ids(groups):
    """fixture 의 각 URL 항목에 대해 extract_external_ids 결과가 expected 와 일치."""
    mismatches: list[str] = []
    for group in groups:
        for item in group["urls"]:
            url = item["url"]
            expected = [
                (e["kind"], e["value"]) for e in item["expected_external_ids"]
            ]
            actual = [
                (x.kind, x.value) for x in extract_external_ids(url=url)
            ]
            if sorted(actual) != sorted(expected):
                mismatches.append(
                    f"  {group['name']}/{item['kind']} {url}\n"
                    f"    expected: {expected}\n"
                    f"    actual:   {actual}"
                )
    assert not mismatches, "URL → ext_ids 매핑 불일치:\n" + "\n".join(mismatches)


# ── 2단계: 그룹핑 시뮬레이션 (mock session) ─────────────────


class _MockState:
    def __init__(self) -> None:
        self.topics: dict[str, dict] = {}
        self.links: list[dict] = []

    async def find_or_create(self, session, *, slug, title, primary_external_id):
        if slug in self.topics:
            return self.topics[slug], False
        t = {
            "id": uuid4(), "slug": slug, "title": title,
            "primary_external_id": primary_external_id,
        }
        self.topics[slug] = t
        return t, True

    async def link(self, session, *, item_id, topic_id, role,
                   confidence=1.0, source="auto", note=None):
        self.links.append({
            "item_id": item_id, "topic_id": topic_id,
            "role": role, "confidence": confidence,
        })
        return True

    def slug_of_topic_id(self, topic_id: UUID) -> str:
        for slug, t in self.topics.items():
            if t["id"] == topic_id:
                return slug
        return ""


@pytest.fixture
def state(monkeypatch) -> _MockState:
    s = _MockState()
    monkeypatch.setattr(url_module, "find_or_create_topic", s.find_or_create)
    monkeypatch.setattr(url_module, "link_item_to_topic", s.link)
    return s


def _ids_for_item(group: dict, item: dict) -> list[ExternalId]:
    """ingest 시 추출되는 ext_ids 시뮬레이션.

    project_page (URL 자체엔 식별자 없음) 은 본문에서 같은 group 내 다른 URL 들의
    식별자를 발견하는 시나리오로 처리 — 실제 ingest 의 본문 link 검출과 동일 효과.
    """
    direct = extract_external_ids(url=item["url"])
    if item["kind"] == "project_page":
        # 본문 안에 같은 그룹의 paper/code 링크가 있는 케이스
        body = " ".join(other["url"] for other in group["urls"] if other != item)
        return extract_external_ids(url=item["url"], text=body)
    if item["kind"] == "code":
        # GitHub README 가 같은 그룹의 paper 를 참조하는 케이스
        paper_links = [
            other["url"] for other in group["urls"] if other["kind"] == "paper"
        ]
        return extract_external_ids(url=item["url"], text=" ".join(paper_links))
    return direct


@pytest.mark.asyncio
async def test_group_items_share_topics(state: _MockState, groups):
    """fixture 의 각 그룹 안 item 들이 expected_shared_topics 에 모두 link 되어야."""
    item_ids: dict[str, UUID] = {}
    for group in groups:
        for item in group["urls"]:
            iid = uuid4()
            item_ids[item["url"]] = iid
            ids = _ids_for_item(group, item)
            role_kind = {
                "paper": "url",
                "code": "github",
                "project_page": "url",
            }[item["kind"]]
            await url_module.auto_link_topics(
                session=None, item_id=iid, source_type=role_kind,
                title=item["url"], ids=ids,
            )

    for group in groups:
        expected_shared = group["expected_shared_topics"]
        if not expected_shared:
            continue  # 그룹핑이 없어야 하는 control 그룹은 별도 테스트에서 검증
        group_item_ids = {item_ids[item["url"]] for item in group["urls"]}
        for shared_slug in expected_shared:
            assert shared_slug in state.topics, (
                f"{group['name']} 의 shared topic {shared_slug!r} 가 생성 안 됨"
            )
            topic_id = state.topics[shared_slug]["id"]
            linked_items = {
                link["item_id"] for link in state.links if link["topic_id"] == topic_id
            }
            missing = group_item_ids - linked_items
            assert not missing, (
                f"{group['name']} 의 일부 item 이 {shared_slug} 에 link 안 됨: {missing}"
            )


@pytest.mark.asyncio
async def test_separate_groups_dont_collide(state: _MockState, groups):
    """다른 group 의 item 은 같은 topic 을 공유하면 안 됨."""
    by_group: dict[str, set[UUID]] = {}
    for group in groups:
        for item in group["urls"]:
            iid = uuid4()
            by_group.setdefault(group["name"], set()).add(iid)
            ids = _ids_for_item(group, item)
            await url_module.auto_link_topics(
                session=None, item_id=iid, source_type="url",
                title=item["url"], ids=ids,
            )

    # 같은 topic 에 두 그룹 item 이 같이 있는지 체크
    for topic_id in {t["id"] for t in state.topics.values()}:
        linked_items = {
            link["item_id"] for link in state.links if link["topic_id"] == topic_id
        }
        groups_touched = {
            name for name, ids in by_group.items() if linked_items & ids
        }
        assert len(groups_touched) <= 1, (
            f"topic {state.slug_of_topic_id(topic_id)} 에 여러 그룹이 섞임: {groups_touched}"
        )


def test_primary_external_id_priority_for_amber():
    """amber 의 ext_ids [arxiv, github] → arxiv 가 primary (priority 0)."""
    ids = [
        ExternalId("github", "HengyiWang/amb3r"),
        ExternalId("arxiv", "2511.20343"),
    ]
    p = primary_external_id(ids)
    assert p is not None
    assert p.kind == "arxiv"
    assert p.slug == "arxiv:2511.20343"
