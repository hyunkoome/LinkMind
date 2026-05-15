"""
backend.ingest.github 의 정적 흐름 검증 — GitHub API 응답을 httpx mock 으로 가짜.

확인:
- raw_body 가 stars/forks (변동 값) 를 포함하지 않음 → idempotent 보장
- license SPDX 가 paper_keywords 의 맨 앞에 → license tag 가 _TAG_MAX 안에 살아남음
- README 의 arxiv 링크 → external_ids 에 cross-modal 단서 포함

DB/LLM 접속 없이 — ingest_github 의 내부 비즈니스 로직만 한 번에.
"""

from __future__ import annotations

import base64

import pytest

from backend.ingest import github as gh_module


FAKE_META = {
    "name": "LoRA",
    "full_name": "microsoft/LoRA",
    "description": "Code for the paper LoRA: Low-Rank Adaptation of Large Language Models.",
    "language": "Python",
    "topics": ["lora", "adaptation", "deep-learning", "gpt-3", "language-model"],
    "stargazers_count": 12345,                        # 시간에 따라 변함 — raw_body 에 들어가면 안 됨
    "forks_count": 678,
    "license": {"spdx_id": "MIT", "name": "MIT License"},
    "homepage": "",
    "default_branch": "main",
}

FAKE_README_TEXT = (
    "# LoRA: Low-Rank Adaptation\n\n"
    "Paper: https://arxiv.org/abs/2106.09685\n"
    "Companion repo info — see also https://github.com/foo/related.\n"
)

FAKE_LANGUAGES = {"Python": 12345, "Shell": 678}


async def _fake_gh_get(client, path: str):
    """gh_module._gh_get 의 mock — 실제 httpx client 는 안 쓰고 path 만 보고 가짜 응답."""
    if path == "/repos/microsoft/LoRA":
        return FAKE_META
    if path == "/repos/microsoft/LoRA/languages":
        return FAKE_LANGUAGES
    if path == "/repos/microsoft/LoRA/readme":
        return {
            "path": "README.md",
            "content": base64.b64encode(FAKE_README_TEXT.encode()).decode(),
            "encoding": "base64",
        }
    return None


@pytest.mark.asyncio
async def test_github_static_pipeline_with_mocked_api(monkeypatch):
    """ingest_github 의 _save_with_summary 까지 가기 전 단계를 직접 호출.

    `_save_with_summary` 는 DB 접속이 필요하므로 monkeypatch 로 no-op 으로 만들어
    DB 없이 raw_body / ext_ids 구성만 검증한다. `_gh_get` 도 monkeypatch — 네트워크 안 탐.
    """
    captured: dict = {}

    async def fake_save(**kwargs):
        captured.update(kwargs)
        return {"item_id": "00000000-0000-0000-0000-000000000000", "created": True}

    monkeypatch.setattr(gh_module, "_save_with_summary", fake_save)
    monkeypatch.setattr(gh_module, "_gh_get", _fake_gh_get)

    await gh_module.ingest_github("https://github.com/microsoft/LoRA", analyze_now=True)

    assert captured["source_type"] == "github"
    assert captured["source_id"] == "microsoft/LoRA"

    body: str = captured["doc"].body
    # 가변 카운터가 raw_body 에 들어가면 안 됨
    assert "12345" not in body, "stars 가 raw_body 에 남아 있음 — idempotent 깨짐"
    assert "Stars:" not in body
    assert "Forks:" not in body
    # 정렬된 topics (sorted topics) 로 안정적인 hash
    body_topics_line = next(ln for ln in body.splitlines() if ln.startswith("Topics:"))
    assert body_topics_line == "Topics: " + ", ".join(sorted(FAKE_META["topics"]))

    # license tag 가 paper_keywords 의 맨 앞 — _TAG_MAX 안에 살아남는 우선순위 보장
    paper_keywords = captured["doc"].paper_keywords
    assert paper_keywords[0] == "MIT"

    # external_ids — self (github:microsoft/LoRA) + arxiv (2106.09685) + 다른 github 링크.
    ext_ids = captured["external_ids"]
    kinds = {x.kind for x in ext_ids}
    assert "github" in kinds
    assert "arxiv" in kinds
    arxiv_vals = [x.value for x in ext_ids if x.kind == "arxiv"]
    assert "2106.09685" in arxiv_vals
    github_vals = [x.value for x in ext_ids if x.kind == "github"]
    # 첫 번째는 자기 자신, 그 다음 README 안의 다른 repo
    assert github_vals[0] == "microsoft/LoRA"
    assert "foo/related" in github_vals
