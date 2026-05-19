"""
backend.ingest.slack 의 export 파서 + helper 단위 테스트.

실 fixture: tests/resources/slack_export_sample/ (slackdump standard 구조 모사,
1 channel · 8 messages). 케이스:
  - channel_join subtype skip
  - thread 부모 → 자식 parent_text 전파
  - mrkdwn `<url|label>` / blocks link / raw URL dedup
  - 일반 URL 메시지 + caption 분리
  - 일반 텍스트 note
  - 너무 짧은 text skip
  - 첨부 파일 매핑

DB/네트워크 없음 — 파서/caption 로직만. ingest_slack_message 실제 호출은
integration 마커로 분리.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.ingest.slack import (
    SlackAttachment,
    SlackMessage,
    clean_mrkdwn_text,
    extract_attachments,
    extract_urls,
    parse_slack_export,
)
from backend.ingest.slack import _resolve_caption  # 내부 helper도 검증
from backend.ingest.slack.export_parser import (
    _slack_permalink,
    _ts_to_datetime,
    load_channels_meta,
    load_users_meta,
)


FIXTURE = Path(__file__).parent / "resources" / "slack_export_sample"


# ──────────────────────────────────────────────────────────────
# clean_mrkdwn_text
# ──────────────────────────────────────────────────────────────


def test_clean_mrkdwn_link_with_label():
    assert clean_mrkdwn_text("<https://x.com/foo|Foo Site>") == "Foo Site"


def test_clean_mrkdwn_link_without_label():
    assert clean_mrkdwn_text("<https://arxiv.org/abs/2106.09685>") == "https://arxiv.org/abs/2106.09685"


def test_clean_mrkdwn_user_mention_removed():
    assert clean_mrkdwn_text("<@U06QA9GMQSD> hello") == "hello"


def test_clean_mrkdwn_channel_ref():
    assert clean_mrkdwn_text("see <#C123|general> please") == "see #general please"


def test_clean_mrkdwn_special_here():
    assert clean_mrkdwn_text("<!here> 주목") == "@here 주목"


def test_clean_mrkdwn_subteam_strips_id():
    assert clean_mrkdwn_text("<!subteam^S123|@team> ping") == "@subteam ping"


def test_clean_mrkdwn_html_entities():
    assert clean_mrkdwn_text("A &amp; B &lt;test&gt;") == "A & B <test>"


def test_clean_mrkdwn_empty():
    assert clean_mrkdwn_text("") == ""
    assert clean_mrkdwn_text(None) == ""  # type: ignore[arg-type]


def test_clean_mrkdwn_whitespace_normalized():
    assert clean_mrkdwn_text("  multi    spaces\n\nand newlines ") == "multi spaces and newlines"


# ──────────────────────────────────────────────────────────────
# extract_urls (blocks + mrkdwn + raw, dedup)
# ──────────────────────────────────────────────────────────────


def test_extract_urls_from_blocks():
    msg = {
        "text": "some text",
        "blocks": [{
            "type": "rich_text",
            "elements": [{
                "type": "rich_text_section",
                "elements": [{"type": "link", "url": "https://arxiv.org/abs/2106.09685"}],
            }],
        }],
    }
    assert extract_urls(msg) == ["https://arxiv.org/abs/2106.09685"]


def test_extract_urls_from_mrkdwn_text_only():
    msg = {"text": "<https://github.com/foo/bar|repo>"}
    assert extract_urls(msg) == ["https://github.com/foo/bar"]


def test_extract_urls_from_raw_text():
    msg = {"text": "참고: https://arxiv.org/abs/2401.01234 보세요"}
    assert extract_urls(msg) == ["https://arxiv.org/abs/2401.01234"]


def test_extract_urls_dedup_blocks_and_mrkdwn():
    """blocks 와 mrkdwn 양쪽에 같은 URL 이 있어도 한 번만."""
    msg = {
        "text": "<https://arxiv.org/abs/2106.09685|paper>",
        "blocks": [{
            "type": "rich_text",
            "elements": [{
                "type": "rich_text_section",
                "elements": [{"type": "link", "url": "https://arxiv.org/abs/2106.09685"}],
            }],
        }],
    }
    assert extract_urls(msg) == ["https://arxiv.org/abs/2106.09685"]


def test_extract_urls_strips_trailing_punctuation():
    msg = {"text": "see https://github.com/foo/bar."}
    assert extract_urls(msg) == ["https://github.com/foo/bar"]


def test_extract_urls_empty():
    assert extract_urls({}) == []
    assert extract_urls({"text": "no urls here at all"}) == []


# ──────────────────────────────────────────────────────────────
# extract_attachments
# ──────────────────────────────────────────────────────────────


def test_extract_attachments_existing_file():
    atts = extract_attachments(
        {"files": [{"id": "FTESTVID001", "name": "demo.mp4", "mimetype": "video/mp4", "size": 27}]},
        FIXTURE / "attachments",
    )
    assert len(atts) == 1
    a = atts[0]
    assert isinstance(a, SlackAttachment)
    assert a.file_name == "demo.mp4"
    assert a.file_id == "FTESTVID001"
    assert a.mime_type == "video/mp4"
    assert Path(a.file_path).exists()


def test_extract_attachments_missing_file_skipped(tmp_path: Path):
    """slackdump 다운로드 실패한 경우 — 메타만 있고 파일 없으면 skip."""
    atts = extract_attachments(
        {"files": [{"id": "FNOTFOUND", "name": "missing.bin", "size": 100}]},
        tmp_path,
    )
    assert atts == []


def test_extract_attachments_no_files_field():
    assert extract_attachments({}, FIXTURE / "attachments") == []


# ──────────────────────────────────────────────────────────────
# helper
# ──────────────────────────────────────────────────────────────


def test_ts_to_datetime_valid():
    dt = _ts_to_datetime("1720622606.581589")
    assert dt is not None
    assert dt.year == 2024
    assert dt.month == 7


def test_ts_to_datetime_invalid_returns_none():
    assert _ts_to_datetime("not-a-number") is None
    assert _ts_to_datetime("") is None


def test_slack_permalink_basic():
    url = _slack_permalink("https://hkkim.slack.com", "C123", "1720622606.581589")
    assert url == "https://hkkim.slack.com/archives/C123/p1720622606581589"


def test_slack_permalink_strips_trailing_slash():
    url = _slack_permalink("https://hkkim.slack.com/", "C123", "1.000001")
    assert url == "https://hkkim.slack.com/archives/C123/p1000001"


def test_slack_permalink_returns_none_for_missing():
    assert _slack_permalink(None, "C123", "1.0") is None
    assert _slack_permalink("https://x.slack.com", None, "1.0") is None
    assert _slack_permalink("https://x.slack.com", "C123", "") is None


# ──────────────────────────────────────────────────────────────
# Meta 로더
# ──────────────────────────────────────────────────────────────


def test_load_channels_meta():
    meta = load_channels_meta(FIXTURE)
    assert "test-channel" in meta
    assert meta["test-channel"]["id"] == "C06QLDC2G72"


def test_load_users_meta():
    users = load_users_meta(FIXTURE)
    assert users["U06QA9GMQSD"] == "hkkim"


def test_load_channels_meta_missing_file(tmp_path: Path):
    assert load_channels_meta(tmp_path) == {}


def test_load_users_meta_missing_file(tmp_path: Path):
    assert load_users_meta(tmp_path) == {}


# ──────────────────────────────────────────────────────────────
# parse_slack_export — 실 fixture (8 메시지)
# ──────────────────────────────────────────────────────────────


def _parse_all() -> list[SlackMessage]:
    return list(parse_slack_export(FIXTURE, workspace_url="https://hkkim.slack.com"))


def test_parse_skips_channel_join_subtype():
    """channel_join subtype 메시지는 결과에 없어야."""
    msgs = _parse_all()
    # 8 message 중 1 (channel_join) skip + 1 (text 'ㅋ' 인데 URL/첨부 없고 len<20 도 아님 —
    # 사실 'ㅋ' 1자라서 cleaned + no urls + no atts → 정보 0 이라 skip).
    # 또한 channel_join 도 skip 이라 결과는 6개.
    subtypes = [m.subtype for m in msgs]
    assert "channel_join" not in subtypes


def test_parse_yields_six_meaningful_messages():
    """8 메시지 = channel_join (skip) + thread parent + 3 thread children +
    standalone URL + note + 'ㅋ' (정보 0 이지만 cleaned 'ㅋ' 는 있으므로 yield) +
    첨부. cleaned 'ㅋ' 는 URL/첨부 없지만 text 있어 yield. 즉 7개.
    """
    msgs = _parse_all()
    # channel_join 1개만 skip → 7개
    assert len(msgs) == 7


def test_parse_thread_parent_marked():
    msgs = _parse_all()
    parents = [m for m in msgs if m.is_thread_parent]
    assert len(parents) == 1
    assert "LoRA" in parents[0].text


def test_parse_thread_children_inherit_parent_text():
    msgs = _parse_all()
    children = [m for m in msgs if m.thread_ts and not m.is_thread_parent]
    assert len(children) == 2  # arxiv + github
    for c in children:
        assert c.parent_text is not None
        assert "LoRA" in c.parent_text


def test_parse_extracts_url_from_thread_child():
    msgs = _parse_all()
    children = [m for m in msgs if m.thread_ts and not m.is_thread_parent]
    urls = [u for c in children for u in c.urls]
    assert "https://arxiv.org/abs/2106.09685" in urls
    assert "https://github.com/microsoft/LoRA" in urls


def test_parse_standalone_url_no_parent_text():
    msgs = _parse_all()
    standalone = next(m for m in msgs if m.ts == "1747600200.000200")
    assert standalone.thread_ts is None
    assert standalone.parent_text is None
    assert "https://arxiv.org/abs/2401.01234" in standalone.urls
    # cleaned text 는 link 라벨 제거된 나머지
    assert "참고 자료" in standalone.text


def test_parse_note_message():
    msgs = _parse_all()
    note = next(m for m in msgs if m.ts == "1747600300.000300")
    assert note.urls == []
    assert note.attachments == []
    assert "sVLL LoRA" in note.text
    assert len(note.text) >= 20


def test_parse_attachment_message():
    msgs = _parse_all()
    att_msg = next(m for m in msgs if m.ts == "1747600500.000500")
    assert len(att_msg.attachments) == 1
    assert att_msg.attachments[0].file_name == "demo.mp4"


def test_parse_user_display_from_profile():
    msgs = _parse_all()
    parent = next(m for m in msgs if m.is_thread_parent)
    assert parent.user == "hkkim"
    assert parent.user_id == "U06QA9GMQSD"


def test_parse_channel_id_resolved_from_channels_json():
    msgs = _parse_all()
    assert all(m.channel_id == "C06QLDC2G72" for m in msgs)
    assert all(m.channel == "test-channel" for m in msgs)


def test_parse_permalink_populated_when_workspace_given():
    msgs = _parse_all()
    parent = next(m for m in msgs if m.is_thread_parent)
    assert parent.permalink is not None
    assert "hkkim.slack.com/archives/C06QLDC2G72/p" in parent.permalink


def test_parse_no_workspace_no_permalink():
    msgs = list(parse_slack_export(FIXTURE))   # workspace_url 안 줌
    assert all(m.permalink is None for m in msgs)


def test_parse_channel_filter_includes_only_matching():
    msgs = list(parse_slack_export(FIXTURE, channel_filter="test-channel"))
    assert len(msgs) > 0
    msgs_other = list(parse_slack_export(FIXTURE, channel_filter="does-not-exist"))
    assert msgs_other == []


def test_parse_invalid_dir_raises(tmp_path: Path):
    # tmp_path 안에 디렉토리 아닌 파일만
    bad = tmp_path / "notdir.txt"
    bad.write_text("x")
    with pytest.raises(ValueError, match="slack export"):
        list(parse_slack_export(bad))


# ──────────────────────────────────────────────────────────────
# _resolve_caption — ingest 로직의 핵심 분기
# ──────────────────────────────────────────────────────────────


def _msg(**kw) -> SlackMessage:
    """SlackMessage 기본값 채워서 만들기."""
    defaults = dict(
        ts="1.0", date=None, channel="c", channel_id=None,
        text="", raw_text="",
    )
    defaults.update(kw)
    return SlackMessage(**defaults)


def test_resolve_caption_thread_child_uses_parent_text():
    m = _msg(
        text="https://arxiv.org/abs/2106.09685",
        thread_ts="123.0", is_thread_parent=False,
        parent_text="☆ LoRA paper",
        urls=["https://arxiv.org/abs/2106.09685"],
    )
    assert _resolve_caption(m) == "☆ LoRA paper"


def test_resolve_caption_thread_parent_strips_own_urls():
    """thread 부모인데 자기 본문에도 URL 이 있으면 — caption 은 URL 빼고 남은 부분."""
    m = _msg(
        text="좋은 자료 https://x.com/foo",
        thread_ts="1.0", is_thread_parent=True,
        urls=["https://x.com/foo"],
    )
    assert _resolve_caption(m) == "좋은 자료"


def test_resolve_caption_attachment_keeps_text():
    m = _msg(
        text="video attached",
        attachments=[SlackAttachment(
            file_path="/tmp/x", file_name="x.mp4", file_id="F1",
            mime_type="video/mp4", size=1,
        )],
    )
    assert _resolve_caption(m) == "video attached"


def test_resolve_caption_url_only_returns_none():
    """URL 뿐인 메시지 — caption 만들 만한 메모 없음."""
    m = _msg(
        text="https://x.com",
        urls=["https://x.com"],
    )
    assert _resolve_caption(m) is None


def test_resolve_caption_plain_text_returns_none():
    """URL/첨부/thread 부모 정보 없는 일반 메시지는 caption 없음 (note 로 저장됨)."""
    m = _msg(text="hello there")
    assert _resolve_caption(m) is None
