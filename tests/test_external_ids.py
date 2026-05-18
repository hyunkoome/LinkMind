"""
backend.utils.external_ids — URL/텍스트에서 arxiv/doi/github/youtube 식별자 추출 테스트.
"""

from __future__ import annotations

from backend.utils.external_ids import (
    ExternalId,
    arxiv_id_from_url,
    arxiv_ids_from_text,
    doi_from_url,
    dois_from_text,
    extract_external_ids,
    github_repo_from_url,
    github_repos_from_text,
    normalize_arxiv_id,
    primary_external_id,
    youtube_ids_from_url,
)


# ── arxiv ───────────────────────────────────────────────────


def test_arxiv_from_abs_url():
    assert arxiv_id_from_url("https://arxiv.org/abs/2106.09685") == "2106.09685"


def test_arxiv_strips_version():
    assert arxiv_id_from_url("https://arxiv.org/abs/2106.09685v3") == "2106.09685"


def test_arxiv_from_pdf_url():
    assert arxiv_id_from_url("https://arxiv.org/pdf/2106.09685.pdf") == "2106.09685"
    assert arxiv_id_from_url("https://arxiv.org/pdf/2106.09685v2.pdf") == "2106.09685"


def test_arxiv_non_arxiv_host_returns_none():
    assert arxiv_id_from_url("https://example.com/paper/2106.09685") is None


def test_arxiv_from_text_multiple():
    text = "See arXiv:2106.09685 and 1706.03762 — older paper hep-th/0507214 too."
    out = arxiv_ids_from_text(text)
    assert "2106.09685" in out
    assert "1706.03762" in out
    # 구형 (subj/YYMMNNN) 도 인식
    assert "hep-th/0507214" in out


def test_normalize_arxiv_id_variants():
    assert normalize_arxiv_id("arXiv:2106.09685") == "2106.09685"
    assert normalize_arxiv_id("2106.09685v2") == "2106.09685"
    assert normalize_arxiv_id("nothing here") is None


# ── doi ─────────────────────────────────────────────────────


def test_doi_from_doi_org_url():
    assert doi_from_url("https://doi.org/10.1145/3411764.3445555") == "10.1145/3411764.3445555"
    assert doi_from_url("https://dx.doi.org/10.1145/3411764.3445555") == "10.1145/3411764.3445555"


def test_doi_from_text():
    text = "Published in CHI; DOI 10.1145/3411764.3445555 — see crossref."
    out = dois_from_text(text)
    assert "10.1145/3411764.3445555" in out


def test_doi_invalid_pattern_ignored():
    # 'xxx' 는 숫자 아니므로 invalid
    assert dois_from_text("doi.org/10.xxx/yyy") == []


# ── github ──────────────────────────────────────────────────


def test_github_from_url_basic():
    assert github_repo_from_url("https://github.com/microsoft/LoRA") == "microsoft/LoRA"


def test_github_strips_git_suffix():
    assert github_repo_from_url("https://github.com/microsoft/LoRA.git") == "microsoft/LoRA"


def test_github_ignores_extra_path():
    assert github_repo_from_url(
        "https://github.com/microsoft/LoRA/tree/main/src"
    ) == "microsoft/LoRA"


def test_github_non_github_host():
    assert github_repo_from_url("https://gitlab.com/foo/bar") is None


def test_github_from_text_multiple():
    text = "code: https://github.com/foo/bar — also https://github.com/baz/qux.git"
    out = github_repos_from_text(text)
    assert "foo/bar" in out
    assert "baz/qux" in out


def test_github_attachment_url_not_repo():
    """github.com/user-attachments/assets/<uuid> 는 attachment URL — repo 아님."""
    url = "https://github.com/user-attachments/assets/abc-123-def"
    assert github_repo_from_url(url) is None


def test_github_orgs_path_not_repo():
    """github.com/orgs/<org> 는 org 페이지 — repo 아님."""
    assert github_repo_from_url("https://github.com/orgs/anthropic/teams") is None


def test_github_reserved_owners_rejected():
    """settings / sponsors / search 등 system path 도 owner 로 인식 X."""
    for path in ("settings/profile", "sponsors/foo", "search/results",
                 "marketplace/actions", "explore/topics"):
        assert github_repo_from_url(f"https://github.com/{path}") is None


def test_github_invalid_owner_starts_with_hyphen():
    """username 규칙: 시작/끝 hyphen X."""
    assert github_repo_from_url("https://github.com/-bad/repo") is None
    assert github_repo_from_url("https://github.com/bad-/repo") is None


def test_github_repos_from_text_skips_attachments():
    """텍스트 안의 attachment URL 도 추출 결과에 안 들어감 (PDF/README 본문 파싱 시)."""
    text = """좋은 자료: https://github.com/microsoft/LoRA
    캡처: https://github.com/user-attachments/assets/uuid-xxx
    다른 repo: https://github.com/foo/bar"""
    repos = github_repos_from_text(text)
    assert "microsoft/LoRA" in repos
    assert "foo/bar" in repos
    assert not any(r.startswith("user-attachments/") for r in repos)


# ── youtube ─────────────────────────────────────────────────


def test_youtube_watch_video():
    assert youtube_ids_from_url(
        "https://www.youtube.com/watch?v=PYr-LSOf2OY"
    ) == ("PYr-LSOf2OY", None)


def test_youtube_watch_video_with_playlist():
    assert youtube_ids_from_url(
        "https://www.youtube.com/watch?v=PYr-LSOf2OY&list=PL5Q2soXY"
    ) == ("PYr-LSOf2OY", "PL5Q2soXY")


def test_youtube_playlist_only():
    assert youtube_ids_from_url(
        "https://www.youtube.com/playlist?list=PL5Q2soXY"
    ) == (None, "PL5Q2soXY")


def test_youtu_be_short():
    assert youtube_ids_from_url("https://youtu.be/abc12345xyz") == ("abc12345xyz", None)


# ── 통합 extract_external_ids + primary ──────────────────────


def test_extract_url_and_text_combined():
    ids = extract_external_ids(
        url="https://github.com/microsoft/LoRA",
        text="paper: https://arxiv.org/abs/2106.09685 — doi 10.1145/123456.789012",
    )
    kinds = {x.kind for x in ids}
    assert "github" in kinds
    assert "arxiv" in kinds
    assert "doi" in kinds


def test_extract_dedup():
    """URL 과 텍스트에서 같은 식별자가 두 번 나오면 한 번만."""
    ids = extract_external_ids(
        url="https://arxiv.org/abs/2106.09685v2",
        text="see arxiv:2106.09685 for details",
    )
    arxiv_ids = [x for x in ids if x.kind == "arxiv"]
    assert len(arxiv_ids) == 1
    assert arxiv_ids[0].value == "2106.09685"


def test_primary_external_id_priority():
    """arxiv > doi > github > yt > ytpl."""
    ids = [
        ExternalId("github", "foo/bar"),
        ExternalId("arxiv", "2106.09685"),
        ExternalId("doi", "10.x/y"),
    ]
    p = primary_external_id(ids)
    assert p is not None
    assert p.kind == "arxiv"
    assert p.slug == "arxiv:2106.09685"


def test_primary_returns_none_for_empty():
    assert primary_external_id([]) is None
