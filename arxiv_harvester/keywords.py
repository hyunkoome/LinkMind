"""arxiv_harvester.keywords — yaml 키워드 로더 (standalone/CLI 편의).

패키지를 단독으로 쓸 때 "어떤 키워드로 수집할지"를 코드 수정 없이 yaml 로 관리한다.
LinkMind 같은 멀티유저 앱은 이걸 안 쓰고 DB(collection_keywords)로 관리한다 — 둘 다
같은 client.search 코어에 키워드를 주입할 뿐이다.

yaml 형식 (둘 다 허용):
    keywords:
      - gaussian splatting
      - multi-camera SLAM
또는 최상위 리스트:
    - gaussian splatting
    - multi-camera SLAM
"""
from __future__ import annotations

from pathlib import Path

import yaml


def load_keywords(path: str | Path) -> list[str]:
    """yaml 파일에서 키워드 리스트를 읽는다. 빈 문자열·중복은 정리(순서 보존).

    파일이 없거나 형식이 어긋나면 ValueError.
    """
    p = Path(path)
    if not p.exists():
        raise ValueError(f"키워드 yaml 없음: {p}")
    data = yaml.safe_load(p.read_text(encoding="utf-8"))

    if isinstance(data, dict):
        raw = data.get("keywords", [])
    elif isinstance(data, list):
        raw = data
    else:
        raise ValueError(f"키워드 yaml 형식 오류 (dict 또는 list 필요): {p}")

    out: list[str] = []
    seen: set[str] = set()
    for kw in raw or []:
        s = str(kw).strip()
        if s and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    return out
