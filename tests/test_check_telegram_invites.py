"""
ai_agents.check_telegram_invites 의 pure 정리 함수 단위 테스트 (cpu 마커, 외부 의존 X).

API 호출 (Telethon, asyncio) 부분은 외부 Telegram 의존이라 통합 테스트 영역.
여기서는 yaml/cache 정리 로직만 검증 — 라인 보존 / 주석 보존 / edge case.
"""

from __future__ import annotations

import json

import pytest

from ai_agents.check_telegram_invites import (
    apply_cleanup,
    remove_hashes_from_cache,
    remove_invites_from_yaml,
)


# ─── remove_invites_from_yaml ─────────────────────────────────────────────


def test_remove_empty_set_noop():
    """제거할 invite 가 비어있으면 원본 그대로."""
    text = "channels:\n  - invite: https://t.me/+abc\n    delete_after_ingest: true\n"
    out, n = remove_invites_from_yaml(text, set())
    assert out == text
    assert n == 0


def test_remove_invite_not_in_yaml_noop():
    """존재 안 하는 invite 지정 시 원본 그대로 + 제거 수 0."""
    text = "channels:\n  - invite: https://t.me/+abc\n    delete_after_ingest: true\n"
    out, n = remove_invites_from_yaml(text, {"https://t.me/+notthere"})
    assert out == text
    assert n == 0


def test_remove_single_minimal_entry():
    """2줄짜리 minimal entry (invite + delete_after_ingest) 통째 제거."""
    text = (
        "channels:\n"
        "  - invite: https://t.me/+abc\n"
        "    delete_after_ingest: true\n"
        "  - invite: https://t.me/+keep\n"
        "    delete_after_ingest: true\n"
    )
    out, n = remove_invites_from_yaml(text, {"https://t.me/+abc"})
    assert n == 1
    assert "https://t.me/+abc" not in out
    assert "https://t.me/+keep" in out
    # 남은 entry 의 라인은 살아있음
    assert "delete_after_ingest: true" in out


def test_remove_entry_with_optional_name_field():
    """name 필드가 있는 3줄짜리 entry 도 통째 제거."""
    text = (
        "channels:\n"
        "  - invite: https://t.me/+abc\n"
        "    delete_after_ingest: true\n"
        "    name: ChannelA\n"
        "  - invite: https://t.me/+keep\n"
        "    delete_after_ingest: true\n"
    )
    out, n = remove_invites_from_yaml(text, {"https://t.me/+abc"})
    assert n == 1
    assert "ChannelA" not in out
    assert "https://t.me/+keep" in out


def test_remove_multiple_entries():
    """여러 invite 한꺼번에 제거."""
    text = (
        "channels:\n"
        "  - invite: https://t.me/+a\n"
        "    delete_after_ingest: true\n"
        "  - invite: https://t.me/+b\n"
        "    delete_after_ingest: true\n"
        "  - invite: https://t.me/+c\n"
        "    delete_after_ingest: true\n"
    )
    out, n = remove_invites_from_yaml(
        text, {"https://t.me/+a", "https://t.me/+c"},
    )
    assert n == 2
    assert "https://t.me/+a" not in out
    assert "https://t.me/+c" not in out
    assert "https://t.me/+b" in out


def test_remove_preserves_top_level_comments():
    """top-level 의 주석 + key (channels:) 는 entry 제거에 영향받지 않음."""
    text = (
        "# 이 파일은 yaml schema\n"
        "# 라인 보존돼야 함\n"
        "channels:\n"
        "  - invite: https://t.me/+a\n"
        "    delete_after_ingest: true\n"
    )
    out, n = remove_invites_from_yaml(text, {"https://t.me/+a"})
    assert n == 1
    assert "# 이 파일은 yaml schema" in out
    assert "# 라인 보존돼야 함" in out
    assert "channels:" in out
    assert "https://t.me/+a" not in out


def test_remove_preserves_blank_lines_between_entries():
    """entry 사이의 빈 줄은 시각적 구분자 — 다음 entry 의 prefix 로 살아남음."""
    text = (
        "channels:\n"
        "  - invite: https://t.me/+a\n"
        "    delete_after_ingest: true\n"
        "\n"
        "  - invite: https://t.me/+b\n"
        "    delete_after_ingest: true\n"
    )
    out, n = remove_invites_from_yaml(text, {"https://t.me/+a"})
    assert n == 1
    assert "https://t.me/+a" not in out
    # b entry 와 그 앞 빈 줄은 살아있어야
    assert "https://t.me/+b" in out


def test_remove_last_entry_no_trailing_blank():
    """yaml 끝의 마지막 entry 제거 — trailing 부분이 깔끔."""
    text = (
        "channels:\n"
        "  - invite: https://t.me/+keep\n"
        "    delete_after_ingest: true\n"
        "  - invite: https://t.me/+last\n"
        "    delete_after_ingest: true\n"
    )
    out, n = remove_invites_from_yaml(text, {"https://t.me/+last"})
    assert n == 1
    assert "https://t.me/+last" not in out
    assert out.endswith("delete_after_ingest: true\n")


def test_remove_first_entry():
    """첫 entry 제거 — channels: 헤더는 그대로."""
    text = (
        "channels:\n"
        "  - invite: https://t.me/+first\n"
        "    delete_after_ingest: true\n"
        "  - invite: https://t.me/+keep\n"
        "    delete_after_ingest: true\n"
    )
    out, n = remove_invites_from_yaml(text, {"https://t.me/+first"})
    assert n == 1
    assert "https://t.me/+first" not in out
    assert out.startswith("channels:\n")
    assert "https://t.me/+keep" in out


# ─── remove_hashes_from_cache ─────────────────────────────────────────────


def test_cache_remove_empty_set_noop():
    cache = {"abc": 100, "def": 200}
    out, n = remove_hashes_from_cache(cache, set())
    assert out == cache
    assert n == 0
    # 새 dict 반환 (입력 mutation 안 함)
    assert out is not cache


def test_cache_remove_single_hash():
    cache = {"abc": 100, "def": 200, "ghi": 300}
    out, n = remove_hashes_from_cache(cache, {"def"})
    assert n == 1
    assert out == {"abc": 100, "ghi": 300}


def test_cache_remove_multiple_hashes():
    cache = {"a": 1, "b": 2, "c": 3, "d": 4}
    out, n = remove_hashes_from_cache(cache, {"a", "c"})
    assert n == 2
    assert out == {"b": 2, "d": 4}


def test_cache_remove_nonexistent_hash_noop():
    cache = {"abc": 100}
    out, n = remove_hashes_from_cache(cache, {"xyz"})
    assert out == cache
    assert n == 0


def test_cache_does_not_mutate_input():
    cache = {"abc": 100, "def": 200}
    original = dict(cache)
    _ = remove_hashes_from_cache(cache, {"abc"})
    assert cache == original  # 원본 보존


# ─── apply_cleanup (file I/O + backup) ────────────────────────────────────


def test_apply_cleanup_writes_files_and_backups(tmp_path):
    """apply_cleanup 가 yaml + cache 둘 다 정리하고 .bak.<ts> 백업 생성."""
    yaml_path = tmp_path / "channels.yaml"
    cache_path = tmp_path / "cache.json"

    yaml_path.write_text(
        "channels:\n"
        "  - invite: https://t.me/+dead\n"
        "    delete_after_ingest: true\n"
        "  - invite: https://t.me/+keep\n"
        "    delete_after_ingest: true\n",
        encoding="utf-8",
    )
    cache_path.write_text(
        json.dumps({"dead": 111, "keep": 222}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    yaml_n, cache_n = apply_cleanup(
        yaml_path, cache_path, ["https://t.me/+dead"],
    )

    assert yaml_n == 1
    assert cache_n == 1

    # yaml 정리 결과
    new_yaml = yaml_path.read_text(encoding="utf-8")
    assert "https://t.me/+dead" not in new_yaml
    assert "https://t.me/+keep" in new_yaml

    # cache 정리 결과
    new_cache = json.loads(cache_path.read_text(encoding="utf-8"))
    assert "dead" not in new_cache
    assert new_cache == {"keep": 222}

    # 백업 파일 존재 — *.bak.<timestamp> 패턴
    bak_files = list(tmp_path.glob("*.bak.*"))
    bak_names = {f.name for f in bak_files}
    assert any(n.startswith("channels.yaml.bak.") for n in bak_names)
    assert any(n.startswith("cache.json.bak.") for n in bak_names)


def test_apply_cleanup_no_cache_file_skips_silently(tmp_path):
    """cache 파일이 없으면 yaml 만 정리 + cache_n=0."""
    yaml_path = tmp_path / "channels.yaml"
    cache_path = tmp_path / "nope.json"

    yaml_path.write_text(
        "channels:\n"
        "  - invite: https://t.me/+dead\n"
        "    delete_after_ingest: true\n",
        encoding="utf-8",
    )

    yaml_n, cache_n = apply_cleanup(
        yaml_path, cache_path, ["https://t.me/+dead"],
    )
    assert yaml_n == 1
    assert cache_n == 0
    assert not cache_path.exists()


def test_apply_cleanup_empty_invites_list_noop(tmp_path):
    """제거할 invite 가 없으면 백업도 안 만들고 그대로."""
    yaml_path = tmp_path / "channels.yaml"
    original = "channels:\n  - invite: https://t.me/+a\n    delete_after_ingest: true\n"
    yaml_path.write_text(original, encoding="utf-8")

    yaml_n, cache_n = apply_cleanup(yaml_path, tmp_path / "nope.json", [])
    assert yaml_n == 0
    assert cache_n == 0
    assert yaml_path.read_text(encoding="utf-8") == original
    # 백업 파일 만들지 않음
    assert not list(tmp_path.glob("*.bak.*"))
