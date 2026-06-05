"""arxiv_harvester.oai — OAI-PMH arXiv schema 파서 단위 테스트 (네트워크 없음)."""
from __future__ import annotations

from arxiv_harvester.oai import parse_list_records, parse_oai_record
import xml.etree.ElementTree as ET

_OAI = "{http://www.openarchives.org/OAI/2.0/}"

SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
 <ListRecords>
  <record>
   <header><identifier>oai:arXiv.org:2106.09685</identifier><datestamp>2021-10-16</datestamp></header>
   <metadata>
    <arXiv xmlns="http://arxiv.org/OAI/arXiv/">
     <id>2106.09685</id>
     <created>2021-06-17</created>
     <updated>2021-10-16</updated>
     <authors>
       <author><keyname>Hu</keyname><forenames>Edward J.</forenames></author>
       <author><keyname>Shen</keyname><forenames>Yelong</forenames></author>
     </authors>
     <title>LoRA: Low-Rank Adaptation of Large Language Models</title>
     <categories>cs.CL cs.AI cs.LG</categories>
     <abstract>We propose Low-Rank Adaptation.</abstract>
     <doi>10.1234/x</doi>
    </arXiv>
   </metadata>
  </record>
  <record>
   <header status="deleted"><identifier>oai:arXiv.org:0000.00000</identifier></header>
  </record>
  <resumptionToken cursor="0" completeListSize="2">TOKEN123</resumptionToken>
 </ListRecords>
</OAI-PMH>"""


def test_parse_oai_record_fields():
    root = ET.fromstring(SAMPLE)
    rec = root.find(f"{_OAI}ListRecords").find(f"{_OAI}record")
    d = parse_oai_record(rec)
    assert d["arxiv_id"] == "2106.09685"
    assert d["title"].startswith("LoRA")
    assert d["abstract"] == "We propose Low-Rank Adaptation."
    assert d["authors"] == ["Edward J. Hu", "Yelong Shen"]   # forenames + keyname
    assert d["categories"] == ["cs.CL", "cs.AI", "cs.LG"]     # 공백 → 배열
    assert d["published"].year == 2021 and d["published"].month == 6
    assert d["updated"].month == 10
    assert d["doi"] == "10.1234/x"
    assert d["source"] == "oai"


def test_parse_list_records_skips_deleted_and_returns_token():
    records, token = parse_list_records(SAMPLE)
    # deleted 레코드는 제외 → 1건만
    assert len(records) == 1
    assert records[0]["arxiv_id"] == "2106.09685"
    assert token == "TOKEN123"


def test_parse_strips_version():
    xml = SAMPLE.replace("<id>2106.09685</id>", "<id>2106.09685v3</id>")
    records, _ = parse_list_records(xml)
    assert records[0]["arxiv_id"] == "2106.09685"   # 버전 strip (DB PK dedup)


def test_parse_no_token_when_empty():
    xml = SAMPLE.replace(
        '<resumptionToken cursor="0" completeListSize="2">TOKEN123</resumptionToken>',
        '<resumptionToken cursor="0" completeListSize="1"/>',
    )
    _, token = parse_list_records(xml)
    assert token is None   # 빈 토큰 → 수확 종료
