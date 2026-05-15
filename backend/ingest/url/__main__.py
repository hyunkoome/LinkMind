"""
CLI 진입점 — `python -m backend.ingest.url [--force] <url> [<url> ...]`

`__init__.py` 의 if __name__ == "__main__": 는 패키지를 -m 으로 실행할 때
호출되지 않으므로(파이썬은 패키지의 경우 __main__.py 를 찾는다), 별도 파일로 분리.

`--force` 옵션은 동일 hash 의 기존 item 도 summary/tags 를 재계산한다
(raw_content/chunks 는 보존). prompt 버전 올린 후 재요약 등에 사용.
"""

from __future__ import annotations

import argparse
import asyncio

from backend.ingest.url import ingest_url


async def _run(urls: list[str], *, force: bool) -> None:
    for u in urls:
        try:
            result = await ingest_url(u, force=force)
            print(f"OK  {u}  →  {result}")
        except Exception as e:  # noqa: BLE001
            print(f"ERR {u}  →  {type(e).__name__}: {e or '(no message)'}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="python -m backend.ingest.url")
    parser.add_argument("--force", action="store_true",
                        help="동일 hash 의 기존 item 도 summary/tags 재계산")
    parser.add_argument("urls", nargs="+", help="ingest 할 URL 1개 이상")
    args = parser.parse_args()
    asyncio.run(_run(args.urls, force=args.force))
