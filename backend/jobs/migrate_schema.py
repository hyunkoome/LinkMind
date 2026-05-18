"""
schema.sql 의 변경사항을 이미 떠 있는 DB 에 적용.

배경:
- `backend/db/schema.sql` 은 Docker Postgres 컨테이너의 docker-entrypoint-initdb.d
  훅에 의해 **첫 부팅 시에만 자동 실행** 된다. 그 후 schema 가 변경되어도 (예:
  새 컬럼 추가) 자동 반영 X.
- 이 스크립트는 schema.sql 전체를 다시 한 번 실행한다. 모든 DDL 이
  `CREATE TABLE IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS` / `CREATE OR REPLACE`
  / `CREATE INDEX IF NOT EXISTS` 라서 여러 번 실행해도 안전 (idempotent).
- ALTER 들은 nullable 이거나 DEFAULT 가 있어 기존 row 에 영향 없음.

사용:
    python -m backend.jobs.migrate_schema
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from sqlalchemy import text

from backend.db.connection import get_engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("linkmind.migrate")


# DDL 을 ';' 단위로 나눠 한 statement 씩 실행. dollar-quoted 함수 body 안의 ';' 는
# 한 statement 로 묶어야 — naive split 의 함정. 간단히 dollar-quote 블록을 한 번
# 통째 보존하고 그 외만 ';' 분리.
_DOLLAR_RE = re.compile(r"\$\$.*?\$\$", re.DOTALL)


def _split_statements(sql: str) -> list[str]:
    """SQL 을 statement 단위로 분리. dollar-quoted body 의 ';' 는 분리 X."""
    placeholder = "<<DOLLAR_BODY_{}>>"
    dollars: list[str] = []

    def _stash(m: re.Match) -> str:
        dollars.append(m.group(0))
        return placeholder.format(len(dollars) - 1)

    masked = _DOLLAR_RE.sub(_stash, sql)
    # 주석 제거 (line comment 만; 단순화)
    masked = re.sub(r"--[^\n]*", "", masked)

    raw_stmts = [s.strip() for s in masked.split(";")]

    def _unstash(s: str) -> str:
        for i, body in enumerate(dollars):
            s = s.replace(placeholder.format(i), body)
        return s

    return [_unstash(s) for s in raw_stmts if s]


async def main() -> None:
    schema_path = Path(__file__).resolve().parent.parent / "db" / "schema.sql"
    sql = schema_path.read_text(encoding="utf-8")
    statements = _split_statements(sql)
    logger.info("schema.sql 적용 — %d statements", len(statements))

    engine = get_engine()
    applied = 0
    async with engine.begin() as conn:
        for i, stmt in enumerate(statements, 1):
            try:
                await conn.execute(text(stmt))
                applied += 1
            except Exception as e:  # noqa: BLE001
                # IF NOT EXISTS / CREATE OR REPLACE 패턴이라 대부분 안전하지만,
                # 만약 실패하면 한 statement 만 fail 처리하고 계속.
                logger.warning("statement %d 실패 (계속): %s\n  %s", i, e, stmt[:200])

    logger.info("✅ migration 완료 (%d / %d statements 적용)", applied, len(statements))


if __name__ == "__main__":
    asyncio.run(main())
