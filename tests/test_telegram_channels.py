"""ai_agents.telegram_channels yaml loader 단위 테스트 (cpu 마커, 외부 의존 X)."""

from __future__ import annotations

import pytest

from ai_agents.telegram_channels import (
    TelegramChannelConfig,
    TelegramWatcherConfig,
    load_telegram_channels,
)


def test_load_missing_yaml_returns_default(tmp_path):
    """yaml 파일 없으면 default TelegramWatcherConfig (빈 channels, default batch_size)."""
    out = load_telegram_channels(tmp_path / "nope.yaml")
    assert isinstance(out, TelegramWatcherConfig)
    assert out.channels == []
    assert out.batch_size == 5


def test_load_empty_yaml_returns_default(tmp_path):
    """완전히 빈 yaml 파일도 default."""
    f = tmp_path / "empty.yaml"
    f.write_text("", encoding="utf-8")
    out = load_telegram_channels(f)
    assert out.channels == []
    assert out.batch_size == 5


def test_load_valid_minimal(tmp_path):
    """필수 필드만 (invite + delete_after_ingest) 있는 minimal yaml."""
    f = tmp_path / "ok.yaml"
    f.write_text(
        "channels:\n"
        "  - invite: https://t.me/+abc\n"
        "    delete_after_ingest: true\n",
        encoding="utf-8",
    )
    out = load_telegram_channels(f)
    assert len(out.channels) == 1
    assert out.batch_size == 5  # default
    assert out.channels[0].invite == "https://t.me/+abc"
    assert out.channels[0].delete_after_ingest is True
    assert out.channels[0].name is None


def test_load_with_name_and_mixed_delete(tmp_path):
    """name 선택 + 채널별 delete 정책 다름."""
    f = tmp_path / "mixed.yaml"
    f.write_text(
        "channels:\n"
        "  - invite: https://t.me/+abc\n"
        "    delete_after_ingest: true\n"
        "    name: MyInbox\n"
        "  - invite: https://t.me/+def\n"
        "    delete_after_ingest: false\n",
        encoding="utf-8",
    )
    out = load_telegram_channels(f)
    assert len(out.channels) == 2
    assert out.channels[0].name == "MyInbox"
    assert out.channels[1].name is None
    assert out.channels[0].delete_after_ingest is True
    assert out.channels[1].delete_after_ingest is False


def test_load_custom_batch_size(tmp_path):
    """yaml 의 batch_size 가 default 를 override."""
    f = tmp_path / "batch.yaml"
    f.write_text(
        "batch_size: 3\n"
        "channels:\n"
        "  - invite: https://t.me/+abc\n"
        "    delete_after_ingest: true\n",
        encoding="utf-8",
    )
    out = load_telegram_channels(f)
    assert out.batch_size == 3


def test_load_batch_size_out_of_range_raises(tmp_path):
    """batch_size 가 1~20 범위 밖이면 RuntimeError (FloodWait 위험)."""
    for bad in (0, -1, 21, 100):
        f = tmp_path / f"bad_{bad}.yaml"
        f.write_text(
            f"batch_size: {bad}\nchannels:\n"
            "  - invite: https://t.me/+abc\n"
            "    delete_after_ingest: true\n",
            encoding="utf-8",
        )
        with pytest.raises(RuntimeError, match="batch_size"):
            load_telegram_channels(f)


def test_load_batch_size_non_int_raises(tmp_path):
    """batch_size 가 정수 아니면 RuntimeError."""
    f = tmp_path / "wrong.yaml"
    f.write_text(
        "batch_size: '5'\n"   # string
        "channels:\n"
        "  - invite: https://t.me/+abc\n"
        "    delete_after_ingest: true\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="batch_size"):
        load_telegram_channels(f)


def test_load_batch_size_bool_raises(tmp_path):
    """yaml 에서 bool 은 int 의 서브타입이라 명시 거부 (True=1 사고 방어)."""
    f = tmp_path / "bool.yaml"
    f.write_text(
        "batch_size: true\n"
        "channels:\n"
        "  - invite: https://t.me/+abc\n"
        "    delete_after_ingest: true\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="batch_size"):
        load_telegram_channels(f)


def test_load_dedup_same_invite(tmp_path):
    """같은 invite 중복은 첫 entry 만 유지 (warning 만 남기고 skip)."""
    f = tmp_path / "dup.yaml"
    f.write_text(
        "channels:\n"
        "  - invite: https://t.me/+abc\n"
        "    delete_after_ingest: true\n"
        "    name: First\n"
        "  - invite: https://t.me/+abc\n"
        "    delete_after_ingest: false\n"
        "    name: Duplicate\n",
        encoding="utf-8",
    )
    out = load_telegram_channels(f)
    assert len(out.channels) == 1
    assert out.channels[0].name == "First"
    assert out.channels[0].delete_after_ingest is True


def test_load_missing_invite_raises(tmp_path):
    """invite 누락 시 RuntimeError."""
    f = tmp_path / "no_invite.yaml"
    f.write_text(
        "channels:\n"
        "  - delete_after_ingest: true\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="invite"):
        load_telegram_channels(f)


def test_load_missing_delete_flag_raises(tmp_path):
    """delete_after_ingest 누락 시 RuntimeError — 필수 필드라 안전한 default 강제 X."""
    f = tmp_path / "no_delete.yaml"
    f.write_text(
        "channels:\n"
        "  - invite: https://t.me/+abc\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="delete_after_ingest"):
        load_telegram_channels(f)


def test_load_delete_flag_non_bool_raises(tmp_path):
    """delete_after_ingest 가 bool 아니면 RuntimeError (운영자 오타 방어)."""
    f = tmp_path / "wrong_type.yaml"
    f.write_text(
        "channels:\n"
        "  - invite: https://t.me/+abc\n"
        "    delete_after_ingest: 'yes'\n",  # string
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="bool"):
        load_telegram_channels(f)


def test_load_channels_not_list_raises(tmp_path):
    """'channels' 가 list 가 아니면 RuntimeError."""
    f = tmp_path / "wrong_top.yaml"
    f.write_text("channels:\n  invite: foo\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="list"):
        load_telegram_channels(f)


def test_load_top_level_no_channels_key_raises(tmp_path):
    """최상위에 'channels' key 없으면 RuntimeError."""
    f = tmp_path / "wrong_root.yaml"
    f.write_text("foo: bar\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="channels"):
        load_telegram_channels(f)


def test_load_yaml_parse_error_raises(tmp_path):
    """깨진 yaml 은 RuntimeError + 'yaml 파싱 실패' 메시지."""
    f = tmp_path / "broken.yaml"
    f.write_text("channels:\n  - invite: [unclosed\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="yaml 파싱 실패"):
        load_telegram_channels(f)


def test_display_name_uses_name_first():
    """display_name() 가 name 우선, 없으면 invite hash 짧게."""
    c = TelegramChannelConfig(invite="https://t.me/+abc", delete_after_ingest=True, name="MyInbox")
    assert c.display_name() == "MyInbox"
    c2 = TelegramChannelConfig(invite="https://t.me/+abcdef", delete_after_ingest=True)
    assert c2.display_name() == "+abcdef"


def test_real_config_file_loads():
    """실제 config/telegram_channels.yaml 정상 로드 (회귀 방지)."""
    from pathlib import Path
    repo_root = Path(__file__).resolve().parent.parent
    yaml_path = repo_root / "config" / "telegram_channels.yaml"
    if not yaml_path.exists():
        pytest.skip("config/telegram_channels.yaml 미존재 (운영자가 아직 안 만듦)")
    out = load_telegram_channels(yaml_path)
    assert len(out.channels) >= 1
    assert 1 <= out.batch_size <= 20
    for c in out.channels:
        assert c.invite.startswith(("https://t.me/", "t.me/", "@")) or "/" not in c.invite
        assert isinstance(c.delete_after_ingest, bool)
