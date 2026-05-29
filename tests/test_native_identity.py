"""
native_identity_external_id 순수 단위 테스트 (D10.6 A2, 2026-05-29).

'1 링크 = 1 위키' — 자료의 정체성(primary) external_id 는 자기 타입/URL 에서만
나와야 한다. 콘텐츠(영상 설명란/README/본문) 에서 발견된 링크는 정체성이 아니라
관계(clue). 옛 동작은 고정 순위(arxiv>doi>github>yt)로만 골라서 youtube 영상의
설명란 github 가 영상 자신의 yt 를 이겨 정체성을 가로채는 중복 wiki 버그가 있었다
([[project-wiki-dedup-diagnosis]]).

DB/네트워크 없는 pure 함수 → tests/ 직접 (cpu 마커 없음, §9 결정 흐름 1).
"""

from __future__ import annotations

from backend.utils.external_ids import ExternalId, native_identity_external_id


def _ids(*pairs: tuple[str, str]) -> list[ExternalId]:
    return [ExternalId(kind=k, value=v) for k, v in pairs]


def test_youtube_with_github_clue_picks_youtube():
    """★ 회귀 ★ youtube 영상 + 설명란 github 링크 → 정체성은 yt (github 아님).

    실제 사례: `Byo7yew9-OQ` ("생성 모델 공부의 핵심") 가 설명란 github 때문에
    github 정체성으로 둔갑하던 버그.
    """
    ids = _ids(("yt", "Byo7yew9-OQ"), ("github", "CodingVillainKor/manim-kor"))
    p = native_identity_external_id(source_type="youtube", url=None, ids=ids)
    assert p is not None
    assert p.kind == "yt"
    assert p.value == "Byo7yew9-OQ"


def test_github_with_arxiv_clue_picks_github():
    """github repo + README 의 arxiv 인용 → 정체성은 github (arxiv 아님).

    옛 동작은 arxiv(rank 0)를 primary 로 골라 repo 가 논문 wiki 에 흡수됐다.
    """
    ids = _ids(("github", "microsoft/LoRA"), ("arxiv", "2106.09685"))
    p = native_identity_external_id(source_type="github", url=None, ids=ids)
    assert p is not None and p.kind == "github" and p.value == "microsoft/LoRA"


def test_pdf_picks_arxiv_over_github():
    """PDF 의 정체성 = 그 논문 (arxiv). 본문 github 는 clue."""
    ids = _ids(("arxiv", "2106.09685"), ("github", "microsoft/LoRA"))
    p = native_identity_external_id(source_type="pdf", url=None, ids=ids)
    assert p is not None and p.kind == "arxiv"


def test_pdf_arxiv_beats_doi():
    """PDF 에 arxiv + doi 둘 다면 arxiv 우선 (primary_external_id rank 유지)."""
    ids = _ids(("doi", "10.1000/xyz"), ("arxiv", "2106.09685"))
    p = native_identity_external_id(source_type="pdf", url=None, ids=ids)
    assert p is not None and p.kind == "arxiv"


def test_pdf_without_paper_id_is_none():
    """PDF 인데 arxiv/doi 없고 본문에 github 만 → 정체성 None (self fallback 대상)."""
    ids = _ids(("github", "owner/repo"))
    p = native_identity_external_id(source_type="pdf", url=None, ids=ids)
    assert p is None


def test_url_identity_from_own_url_only():
    """url-type 의 정체성은 자기 URL 에서. arxiv abstract URL → arxiv."""
    p = native_identity_external_id(
        source_type="url", url="https://arxiv.org/abs/2106.09685", ids=[],
    )
    assert p is not None and p.kind == "arxiv" and p.value == "2106.09685"


def test_url_with_body_clue_ignores_body():
    """★ 회귀 ★ 일반 블로그(자기 URL 엔 식별자 없음) + 본문 arxiv 인용 → 정체성 None.

    본문 링크는 정체성이 아니라 관계 (self_wiki 로 fallback 되어야).
    """
    ids = _ids(("arxiv", "2106.09685"))  # 본문에서 추출된 clue
    p = native_identity_external_id(
        source_type="url", url="https://someblog.com/post", ids=ids,
    )
    assert p is None


def test_url_github_page_picks_github_from_url():
    """github.com URL 이 url-type 으로 들어와도 자기 URL 에서 github 정체성."""
    p = native_identity_external_id(
        source_type="url", url="https://github.com/microsoft/LoRA", ids=[],
    )
    assert p is not None and p.kind == "github" and p.value == "microsoft/LoRA"


def test_telegram_note_no_url_is_none():
    """순수 텍스트 메모 (URL 없음) + 본문 arxiv 인용 → 정체성 None (self).

    메모의 정체성은 메모 자신 — 본문에 언급된 논문이 아님.
    """
    ids = _ids(("arxiv", "2106.09685"))
    p = native_identity_external_id(source_type="telegram", url=None, ids=ids)
    assert p is None


def test_youtube_playlist_picks_ytpl():
    ids = _ids(("ytpl", "PL123"), ("yt", "abc"))
    p = native_identity_external_id(source_type="youtube_playlist", url=None, ids=ids)
    assert p is not None and p.kind == "ytpl"


def test_empty_ids_no_url_is_none():
    assert native_identity_external_id(source_type="url", url=None, ids=[]) is None
