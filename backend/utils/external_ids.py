"""
외부 식별자 추출/정규화 — 같은 "지식 단위" 를 식별하는 단서.

URL/텍스트에서 arxiv_id / doi / github_repo / youtube_video_id /
youtube_playlist_id 를 뽑아내고, topics.slug 로 쓸 정규형으로 변환한다.

용도:
    1. ingest 후 각 item 의 source_metadata 에 표준 external_ids 키를 채움.
    2. 같은 external_id 를 가진 기존 topic 을 찾아 자동 link (auto-grouping).
    3. cross-modal 매칭: GitHub README 의 arxiv 링크 → arxiv item 과 같은 topic.

slug 규칙:
    arxiv:<paper_id>             (paper_id 는 버전 v1/v2 제외)
    doi:<lowercased_doi>
    github:<owner>/<repo>        (대소문자 보존 — GitHub display name)
    yt:<video_id>                (11자 영숫자/하이픈/언더스코어)
    ytpl:<playlist_id>
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import parse_qs, urlparse

ExternalIdKind = str  # 'arxiv' | 'doi' | 'github' | 'yt' | 'ytpl'


@dataclass(frozen=True)
class ExternalId:
    kind: ExternalIdKind
    value: str

    @property
    def slug(self) -> str:
        return f"{self.kind}:{self.value}"


# ──────────────────────────────────────────────────────────────
# arxiv
# ──────────────────────────────────────────────────────────────

# 신형 (2007.04+): YYMM.NNNNN(vN). 구형 (subj/YYMMNNN) 도 일단 같이.
_ARXIV_ID_RE = re.compile(
    r"\b(\d{4}\.\d{4,5})(v\d+)?\b"
    r"|\b([a-z\-]+(?:\.[A-Z]{2})?/\d{7})\b",
    re.IGNORECASE,
)


def normalize_arxiv_id(raw: str) -> str | None:
    """'2106.09685', '2106.09685v2', 'arXiv:2106.09685' 등 → '2106.09685' (버전 제거)."""
    if not raw:
        return None
    m = _ARXIV_ID_RE.search(raw)
    if not m:
        return None
    return (m.group(1) or m.group(3) or "").lower()


def arxiv_id_from_url(url: str) -> str | None:
    """arxiv.org URL 에서 paper id 추출. /abs/, /pdf/, /html/ 모두 처리."""
    if not url:
        return None
    u = urlparse(url)
    if "arxiv.org" not in (u.hostname or "").lower():
        return None
    # path 마지막에서 id 추출 (e.g. /abs/2106.09685v2 또는 /pdf/2106.09685.pdf)
    last = (u.path.rsplit("/", 1) + [""])[-2:]
    cand = last[-1] or last[-2]
    cand = cand.rsplit(".pdf", 1)[0]
    return normalize_arxiv_id(cand)


def arxiv_ids_from_text(text: str, *, limit: int = 10) -> list[str]:
    """본문/README 등 자유 텍스트에서 arxiv id 패턴 추출 (dedup)."""
    if not text:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for m in _ARXIV_ID_RE.finditer(text):
        v = (m.group(1) or m.group(3) or "").lower()
        if v and v not in seen:
            seen.add(v)
            out.append(v)
            if len(out) >= limit:
                break
    return out


# ──────────────────────────────────────────────────────────────
# doi
# ──────────────────────────────────────────────────────────────

# DOI 표준: "10." prefix + registrant + suffix. crossref/datacite 정규식 변형.
_DOI_RE = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Z0-9]+)\b", re.IGNORECASE)


def doi_from_url(url: str) -> str | None:
    if not url:
        return None
    # doi.org URL 또는 일반 URL 내 DOI pattern
    u = urlparse(url)
    host = (u.hostname or "").lower()
    if host in ("doi.org", "dx.doi.org"):
        return u.path.lstrip("/").lower() or None
    m = _DOI_RE.search(url)
    return m.group(1).lower() if m else None


def dois_from_text(text: str, *, limit: int = 10) -> list[str]:
    if not text:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for m in _DOI_RE.finditer(text):
        v = m.group(1).lower()
        # 너무 긴 suffix 는 false positive — 100자 cap
        if len(v) > 100:
            continue
        if v not in seen:
            seen.add(v)
            out.append(v)
            if len(out) >= limit:
                break
    return out


# ──────────────────────────────────────────────────────────────
# github
# ──────────────────────────────────────────────────────────────


def github_repo_from_url(url: str) -> str | None:
    """github.com/<owner>/<repo> → 'owner/repo' (.git suffix 제거, 추가 path 무시)."""
    if not url:
        return None
    u = urlparse(url)
    host = (u.hostname or "").lower()
    if host not in ("github.com", "www.github.com"):
        return None
    parts = [p for p in u.path.split("/") if p]
    if len(parts) < 2:
        return None
    owner, repo = parts[0], parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    # owner/repo 만 — 대소문자 보존 (GitHub display 그대로)
    return f"{owner}/{repo}"


_GITHUB_LINK_RE = re.compile(
    r"https?://(?:www\.)?github\.com/([A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+)",
)


def github_repos_from_text(text: str, *, limit: int = 10) -> list[str]:
    if not text:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for m in _GITHUB_LINK_RE.finditer(text):
        repo = m.group(1)
        # 문장 끝 마침표/쉼표/괄호 등이 repo name 에 끌려들어오는 케이스 정리.
        # 'foo/related.' → 'foo/related', 'foo/related)' → 'foo/related'.
        repo = repo.rstrip(".,;:!?)]}>")
        if repo.endswith(".git"):
            repo = repo[:-4]
        # repo path 자체에 '/' 가 한 번만 있어야 (extra path 가 잡혔으면 첫 owner/repo 만).
        parts = repo.split("/")
        if len(parts) < 2 or not parts[0] or not parts[1]:
            continue
        repo = f"{parts[0]}/{parts[1]}"
        if repo not in seen:
            seen.add(repo)
            out.append(repo)
            if len(out) >= limit:
                break
    return out


# ──────────────────────────────────────────────────────────────
# youtube
# ──────────────────────────────────────────────────────────────


_YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}


def youtube_ids_from_url(url: str) -> tuple[str | None, str | None]:
    """(video_id, playlist_id) — backend.ingest.youtube.parse_youtube_url 와 일관성 유지."""
    if not url:
        return None, None
    u = urlparse(url)
    host = (u.hostname or "").lower()
    if host not in _YT_HOSTS:
        return None, None
    qs = parse_qs(u.query)
    if host == "youtu.be":
        vid = u.path.lstrip("/").split("/")[0] or None
        return vid, qs.get("list", [None])[0]
    if u.path == "/playlist":
        return None, qs.get("list", [None])[0]
    vid: str | None = None
    if u.path == "/watch":
        vid = qs.get("v", [None])[0]
    elif u.path.startswith("/shorts/"):
        vid = u.path.split("/shorts/", 1)[1].split("/")[0] or None
    elif u.path.startswith("/embed/"):
        vid = u.path.split("/embed/", 1)[1].split("/")[0] or None
    return vid, qs.get("list", [None])[0]


# ──────────────────────────────────────────────────────────────
# 종합 추출
# ──────────────────────────────────────────────────────────────


def extract_external_ids(
    *,
    url: str | None = None,
    text: str | None = None,
) -> list[ExternalId]:
    """URL + 자유 텍스트에서 발견 가능한 모든 외부 식별자를 한 번에 추출.

    순서: URL 우선 (가장 강한 신호) → 텍스트. 같은 (kind, value) 는 dedup.
    텍스트에서만 발견된 식별자는 cross-reference 후보 — 자동 link 의 단서.
    """
    out: list[ExternalId] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: str, value: str | None) -> None:
        if not value:
            return
        key = (kind, value)
        if key in seen:
            return
        seen.add(key)
        out.append(ExternalId(kind=kind, value=value))

    if url:
        add("arxiv", arxiv_id_from_url(url))
        add("doi", doi_from_url(url))
        add("github", github_repo_from_url(url))
        vid, plid = youtube_ids_from_url(url)
        add("yt", vid)
        add("ytpl", plid)

    if text:
        for v in arxiv_ids_from_text(text):
            add("arxiv", v)
        for v in dois_from_text(text):
            add("doi", v)
        for v in github_repos_from_text(text):
            add("github", v)

    return out


def role_for_external_id(kind: str, source_type: str) -> str:
    """item 의 source_type + matched external_id kind 로 item_topics.role 추정.

    같은 topic 안에서 modality 구분. UI/검색에서 paper/code/video 묶음으로 표시.
    """
    if source_type == "github":
        return "code"
    if source_type == "pdf":
        return "pdf"
    if source_type == "youtube":
        return "video"
    if source_type == "youtube_playlist":
        return "playlist"
    if source_type == "url":
        # arxiv abstract URL 이면 'paper', 그 외 일반 웹은 'blog'
        return "paper" if kind == "arxiv" else "blog"
    return source_type or "note"


def primary_external_id(ids: Iterable[ExternalId]) -> ExternalId | None:
    """topic.primary_external_id 후보 선택. 우선순위: arxiv > doi > github > yt > ytpl."""
    rank = {"arxiv": 0, "doi": 1, "github": 2, "yt": 3, "ytpl": 4}
    best: ExternalId | None = None
    for x in ids:
        if x.kind not in rank:
            continue
        if best is None or rank[x.kind] < rank[best.kind]:
            best = x
    return best
