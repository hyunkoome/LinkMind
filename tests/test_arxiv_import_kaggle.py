"""backend.jobs.arxiv_import_kaggle — Cornell JSON → arxiv_papers 행 매핑 단위 테스트.

순수 파싱(cornell_record_to_row)만 검증 — DB COPY 는 integration 영역이라 제외.
"""
from __future__ import annotations

from backend.jobs.arxiv_import_kaggle import cornell_record_to_row

CORNELL = {
    "id": "0704.0001",
    "title": "Calculation of prompt\n  diphoton production",
    "abstract": "A fully differential calculation.",
    "categories": "hep-ph cs.LG",
    "doi": "10.1103/x",
    "journal-ref": "Phys.Rev.D76:013009,2007",
    "update_date": "2008-11-26",
    "authors_parsed": [["Balazs", "C.", ""], ["Berger", "E. L.", ""]],
    "versions": [
        {"version": "v1", "created": "Mon, 2 Apr 2007 19:18:42 GMT"},
        {"version": "v2", "created": "Tue, 24 Jul 2007 20:10:27 GMT"},
    ],
}


def test_cornell_mapping():
    row = cornell_record_to_row(CORNELL)
    (arxiv_id, title, abstract, authors, categories, version,
     published, updated, doi, journal_ref, source) = row
    assert arxiv_id == "0704.0001"
    assert title == "Calculation of prompt diphoton production"   # 개행 정리
    assert abstract.startswith("A fully")
    assert authors == ["C. Balazs", "E. L. Berger"]               # "First Last"
    assert categories == ["hep-ph", "cs.LG"]                      # 공백 → 배열
    assert version == "v2"                                        # 최신 버전
    assert published.year == 2007 and published.month == 4        # versions[0].created
    assert updated.year == 2008                                   # update_date
    assert doi == "10.1103/x"
    assert journal_ref.startswith("Phys.Rev")
    assert source == "kaggle"


def test_cornell_skips_missing_title_or_id():
    assert cornell_record_to_row({"id": "1", "title": ""}) is None
    assert cornell_record_to_row({"id": "", "title": "x"}) is None


def test_cornell_graceful_missing_fields():
    # versions/authors_parsed 누락도 graceful (빈 값)
    row = cornell_record_to_row({"id": "1234.5678", "title": "Bare", "abstract": ""})
    assert row is not None
    assert row[0] == "1234.5678"
    assert row[3] == []           # authors
    assert row[4] == []           # categories
    assert row[5] is None         # version
    assert row[6] is None         # published
