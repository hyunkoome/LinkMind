"""
backend.ingest.github.parse_github_url + _slugify 단위 테스트.

특히 license tag 폴백 흐름 (_slugify(license_name)) 이 안정적인지.
"""

from __future__ import annotations

import pytest

from backend.ingest.github import _slugify, parse_github_url


def test_parse_github_url_basic():
    owner, repo = parse_github_url("https://github.com/microsoft/LoRA")
    assert owner == "microsoft"
    assert repo == "LoRA"


def test_parse_github_url_www_subdomain():
    owner, repo = parse_github_url("https://www.github.com/owner/repo")
    assert owner == "owner"
    assert repo == "repo"


def test_parse_github_url_strips_git_suffix():
    owner, repo = parse_github_url("https://github.com/owner/repo.git")
    assert owner == "owner"
    assert repo == "repo"


def test_parse_github_url_extra_path_ignored():
    """`/tree/main/...` 같은 추가 path 가 있어도 owner/repo 만 추출."""
    owner, repo = parse_github_url("https://github.com/owner/repo/tree/main/src")
    assert owner == "owner"
    assert repo == "repo"


def test_parse_github_url_rejects_non_github():
    with pytest.raises(ValueError):
        parse_github_url("https://gitlab.com/owner/repo")


def test_parse_github_url_rejects_missing_repo():
    with pytest.raises(ValueError):
        parse_github_url("https://github.com/microsoft")


# _slugify: license_name 등을 hashtag-safe 슬러그로
def test_slugify_basic():
    assert _slugify("Apache License 2.0") == "Apache-License-2-0"
    assert _slugify("MIT License") == "MIT-License"
    # 한글 보존
    assert _slugify("MIT 라이선스") == "MIT-라이선스"


def test_slugify_strips_outer_dashes():
    assert _slugify("---hello---") == "hello"


def test_slugify_empty():
    assert _slugify("") == ""
    assert _slugify("!!!") == ""
