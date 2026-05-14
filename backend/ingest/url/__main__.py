"""
CLI 진입점 — `python -m backend.ingest.url <url> [<url> ...]`

`__init__.py` 의 if __name__ == "__main__": 는 패키지를 -m 으로 실행할 때
호출되지 않으므로(파이썬은 패키지의 경우 __main__.py 를 찾는다), 별도 파일로 분리.
"""

from __future__ import annotations

import asyncio
import sys

from backend.ingest.url import ingest_url


async def _run() -> None:
    for u in sys.argv[1:]:
        try:
            result = await ingest_url(u)
            print(f"OK  {u}  →  {result}")
        except Exception as e:  # noqa: BLE001
            print(f"ERR {u}  →  {e}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python -m backend.ingest.url <url> [<url> ...]")
        raise SystemExit(2)
    asyncio.run(_run())
