"""
cleanup_duplicate_wikis 의 탐지 헬퍼 순수 단위 테스트 (D10.6 B, 2026-05-29).

_detect_t1 / _detect_t2 / _pick_t1_target 는 native-identity 맵만 받는 pure 함수.
실제 DB/Qdrant 삭제는 integration 영역이라 여기선 탐지 로직(무엇을 지울지)만 검증.

DB/네트워크 없는 pure 함수 → tests/ 직접 (cpu 마커 없음, §9 결정 흐름 1).
"""

from __future__ import annotations

from backend.jobs.cleanup_duplicate_wikis import (
    _detect_t1,
    _detect_t2,
    _pick_t1_target,
)


def _maps(*, wikis: dict[str, dict], item_native: dict[str, set[str]],
          item_source: dict[str, str], item_wikis: dict[str, set[str]]) -> dict:
    """wikis: {slug: {body_status, body, keywords}} → 내부 맵 구성.

    natively_owned = 모든 item 의 native slug 합집합.
    """
    slug_to_wiki = {slug: f"wid-{slug}" for slug in wikis}
    wiki_by_id = {
        f"wid-{slug}": {"slug": slug, "title": slug,
                        "body": w.get("body", ""), "body_status": w.get("body_status", "pending"),
                        "body_model": None, "keywords": w.get("keywords", [])}
        for slug, w in wikis.items()
    }
    natively_owned: set[str] = set()
    for s in item_native.values():
        natively_owned |= s
    # item_wikis 값(slug)을 wiki_id 로 변환
    iw = {iid: {f"wid-{slug}" for slug in slugs} for iid, slugs in item_wikis.items()}
    return {
        "slug_to_wiki": slug_to_wiki,
        "wiki_by_id": wiki_by_id,
        "item_native_slugs": item_native,
        "item_source_type": item_source,
        "natively_owned": natively_owned,
        "item_wikis": iw,
    }


# ──────────────── T1 — self_wiki merge 탐지 ────────────────

def test_t1_detects_self_plus_native():
    """self_wiki + native 외부 wiki 둘 다 보유 → merge 대상."""
    iid = "aaaa"
    self_slug = "url__item__aaaa"
    maps = _maps(
        wikis={self_slug: {}, "yt__vid1": {}},
        item_native={iid: {"yt__vid1"}},
        item_source={iid: "youtube"},
        item_wikis={iid: {self_slug, "yt__vid1"}},
    )
    plan = _detect_t1(maps)
    assert len(plan) == 1
    assert plan[0]["self_wiki"] == "wid-url__item__aaaa"
    assert plan[0]["target"] == "wid-yt__vid1"


def test_t1_skips_self_only_no_native():
    """self_wiki 만 있고 native 외부 wiki 없으면 (일반 블로그) → merge 안 함."""
    iid = "bbbb"
    self_slug = "url__item__bbbb"
    maps = _maps(
        wikis={self_slug: {}},
        item_native={iid: set()},          # URL 에 외부 id 없음
        item_source={iid: "url"},
        item_wikis={iid: {self_slug}},
    )
    assert _detect_t1(maps) == []


def test_t1_skips_native_only_no_self():
    """native wiki 만 있고 self_wiki 없으면 → merge 대상 아님 (이미 깔끔)."""
    iid = "cccc"
    maps = _maps(
        wikis={"github__o-r": {}},
        item_native={iid: {"github__o-r"}},
        item_source={iid: "github"},
        item_wikis={iid: {"github__o-r"}},
    )
    assert _detect_t1(maps) == []


def test_t1_requires_self_wiki_linked():
    """self_wiki 가 존재해도 그 item 에 link 안 돼 있으면 대상 아님."""
    iid = "dddd"
    self_slug = "url__item__dddd"
    maps = _maps(
        wikis={self_slug: {}, "yt__v": {}},
        item_native={iid: {"yt__v"}},
        item_source={iid: "youtube"},
        item_wikis={iid: {"yt__v"}},        # self_wiki 는 link 목록에 없음
    )
    assert _detect_t1(maps) == []


# ──────────────── T1 target 선택 ────────────────

def test_pick_t1_target_single():
    maps = _maps(wikis={"yt__v": {}}, item_native={"i": {"yt__v"}},
                 item_source={"i": "youtube"}, item_wikis={"i": {"yt__v"}})
    assert _pick_t1_target("i", ["wid-yt__v"], maps) == "wid-yt__v"


def test_pick_t1_target_prefers_source_type_native():
    """native wiki 가 여러 개면 자료 source_type 에 맞는 prefix 우선."""
    maps = _maps(
        wikis={"yt__v": {}, "github__o-r": {}},
        item_native={"i": {"yt__v", "github__o-r"}},
        item_source={"i": "youtube"},
        item_wikis={"i": {"yt__v", "github__o-r"}},
    )
    # youtube 자료 → yt__ 우선
    assert _pick_t1_target("i", ["wid-github__o-r", "wid-yt__v"], maps) == "wid-yt__v"


# ──────────────── T2 — phantom 삭제 탐지 ────────────────

def test_t2_detects_unowned_external_wiki():
    """외부 prefix wiki 인데 아무 item 도 native 로 소유 안 함 → phantom."""
    maps = _maps(
        wikis={"github__tensorflow-tensorflow": {}},
        item_native={"i": {"yt__abc"}},     # tensorflow 를 native 로 가진 item 없음
        item_source={"i": "youtube"},
        item_wikis={"i": {"github__tensorflow-tensorflow"}},  # 언급만
    )
    t2 = _detect_t2(maps)
    assert t2 == ["wid-github__tensorflow-tensorflow"]


def test_t2_keeps_natively_owned():
    """누군가 자기 URL 로 직접 ingest 한 외부 wiki 는 보존 (phantom 아님)."""
    maps = _maps(
        wikis={"github__o-r": {}},
        item_native={"i": {"github__o-r"}},   # i 가 직접 소유
        item_source={"i": "github"},
        item_wikis={"i": {"github__o-r"}},
    )
    assert _detect_t2(maps) == []


def test_t2_ignores_self_wiki_prefix():
    """url__item__ self_wiki 는 외부 prefix 아니라 T2 대상 아님 (T1 영역)."""
    maps = _maps(
        wikis={"url__item__xyz": {}},
        item_native={"i": set()},
        item_source={"i": "url"},
        item_wikis={"i": {"url__item__xyz"}},
    )
    assert _detect_t2(maps) == []


def test_t2_ignores_llm_topic_slug():
    """LLM new_pages 의 kebab-case 주제 wiki (외부 prefix 아님) 는 보존."""
    maps = _maps(
        wikis={"lora-fine-tuning": {}},
        item_native={"i": set()},
        item_source={"i": "url"},
        item_wikis={"i": {"lora-fine-tuning"}},
    )
    assert _detect_t2(maps) == []


def test_t1_and_t2_disjoint():
    """같은 dataset 에서 T1(self_wiki)과 T2(외부 phantom)는 겹치지 않음."""
    iid = "ee"
    maps = _maps(
        wikis={
            "url__item__ee": {},
            "yt__own": {},                       # ee 의 native (T1 target)
            "github__phantom-repo": {},          # 아무도 native 소유 X (T2)
        },
        item_native={iid: {"yt__own"}},
        item_source={iid: "youtube"},
        item_wikis={iid: {"url__item__ee", "yt__own", "github__phantom-repo"}},
    )
    t1 = _detect_t1(maps)
    t2 = _detect_t2(maps)
    t1_wikis = {p["self_wiki"] for p in t1} | {p["target"] for p in t1}
    assert "wid-url__item__ee" in {p["self_wiki"] for p in t1}
    assert t2 == ["wid-github__phantom-repo"]
    assert not (t1_wikis & set(t2))      # disjoint
