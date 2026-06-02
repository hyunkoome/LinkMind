"""classifier 의 JSON parse / truncation 복구 단위 테스트.

배경 (2026-06-02): 텔레그램 ingest 의 classifier hook 이 후보 wiki 30개를 매칭할 때
matched 배열이 max_tokens 에 막혀 중간에서 잘려 (truncation) JSON parse 가 3회 모두
실패 → wiki 매핑 linked=0. `_salvage_truncated_json` 이 완성된 matched 요소까지
복구하는지 검증.
"""

from __future__ import annotations

from backend.agents.classifier import _salvage_truncated_json, _try_parse_json


def test_try_parse_plain_json():
    s = '{"matched": [{"wiki_slug": "a", "confidence": 0.9, "role": "primary"}], "new_pages": []}'
    parsed = _try_parse_json(s)
    assert parsed is not None
    assert parsed["matched"][0]["wiki_slug"] == "a"


def test_try_parse_with_code_fence():
    s = '```json\n{"matched": [], "new_pages": []}\n```'
    parsed = _try_parse_json(s)
    assert parsed == {"matched": [], "new_pages": []}


def test_try_parse_fails_on_truncated():
    # 잘린 JSON 은 일반 parse 가 실패해야 한다 (salvage 로 넘어가는 전제)
    s = '{"matched": [{"wiki_slug": "a", "confidence": 0.9, "role": "x"},\n  {"wiki_slug": "b3b1e'
    assert _try_parse_json(s) is None


def test_salvage_recovers_complete_matched_entries():
    """실제 로그에서 본 truncation 패턴 — 첫 객체만 완성, 둘째에서 잘림."""
    s = (
        '```json\n'
        '{\n'
        '  "matched": [\n'
        '    {\n'
        '      "wiki_slug": "url__item__2ce60370-5036-420a-8249-91686396faf7",\n'
        '      "confidence": 0.85,\n'
        '      "role": "context"\n'
        '    },\n'
        '    {\n'
        '      "wiki_slug": "url__item__b3b1e'
    )
    parsed = _salvage_truncated_json(s)
    assert parsed is not None
    matched = parsed["matched"]
    assert len(matched) == 1
    assert matched[0]["wiki_slug"] == "url__item__2ce60370-5036-420a-8249-91686396faf7"
    assert matched[0]["confidence"] == 0.85
    assert matched[0]["role"] == "context"


def test_salvage_recovers_multiple_complete_entries():
    s = (
        '{"matched": ['
        '{"wiki_slug": "a", "confidence": 0.9, "role": "primary"}, '
        '{"wiki_slug": "b", "confidence": 0.8, "role": "context"}, '
        '{"wiki_slug": "c", "confiden'
    )
    parsed = _salvage_truncated_json(s)
    assert parsed is not None
    assert [m["wiki_slug"] for m in parsed["matched"]] == ["a", "b"]


def test_salvage_handles_truncation_inside_string_value():
    # 마지막 element 가 문자열 중간에서 잘려도 직전 완성 element 는 살린다
    s = '{"matched": [{"wiki_slug": "a", "confidence": 0.9, "role": "primary"}, {"wiki_slug": "url__item__bb'
    parsed = _salvage_truncated_json(s)
    assert parsed is not None
    assert len(parsed["matched"]) == 1
    assert parsed["matched"][0]["wiki_slug"] == "a"


def test_salvage_returns_none_when_no_complete_element():
    # 첫 element 조차 완성 안 됐으면 복구 불가 → None
    s = '{"matched": [{"wiki_slug": "url__item__bb'
    assert _salvage_truncated_json(s) is None


def test_salvage_returns_none_on_garbage():
    assert _salvage_truncated_json("그냥 텍스트, JSON 아님") is None
    assert _salvage_truncated_json("") is None
