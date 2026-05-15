"""
GET /files/{file_hash} — attachments 에 저장된 raw 파일을 다운로드/inline 서빙.

PDF 처럼 multipart 업로드된 파일은 원본 URL 이 없으므로 `source_url` 을 이 엔드포인트의
경로(`/files/{file_hash}`) 로 저장한다. Streamlit / 브라우저가 클릭하면 그대로 inline 표시
(PDF 의 경우 viewer 가 뜸). 외부 client 도 file_hash 만 알면 가져갈 수 있음.

보안: 인증 없음 (MVP). 운영 단계에서는 LINKMIND_API_KEY 또는 share-link 토큰으로 보호.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.connection import get_session

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/{file_hash}")
async def serve_file(
    file_hash: str,
    session: AsyncSession = Depends(get_session),
) -> FileResponse:
    """attachments 의 file_path 로 파일 반환 (inline)."""
    # 형식 검증 — SHA-256 hex 만 허용해서 path traversal 방어.
    if not (len(file_hash) == 64 and all(c in "0123456789abcdef" for c in file_hash.lower())):
        raise HTTPException(400, "유효하지 않은 file_hash")

    res = await session.execute(
        text("""
            SELECT file_path, mime_type
            FROM attachments
            WHERE file_hash = :h
            ORDER BY created_at DESC
            LIMIT 1
        """),
        {"h": file_hash},
    )
    row = res.first()
    if not row:
        raise HTTPException(404, "파일 없음")
    file_path, mime_type = row

    p = Path(file_path)
    if not p.exists() or not p.is_file():
        raise HTTPException(410, f"파일이 디스크에 없습니다: {file_path}")

    return FileResponse(
        path=str(p),
        media_type=mime_type or "application/octet-stream",
        filename=f"{file_hash[:12]}{p.suffix}",
        # 브라우저에서 inline 표시 (PDF/이미지). Content-Disposition: inline 으로.
        headers={"Content-Disposition": f'inline; filename="{file_hash[:12]}{p.suffix}"'},
    )
