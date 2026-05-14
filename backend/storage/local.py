"""
로컬 파일시스템 storage 백엔드 (MVP).

원본 파일(이미지/PDF/zip 등)을 raw_file_path로 보존. Phase 2에 MinIO로 교체.
경로 구조: <STORAGE_LOCAL_PATH>/<yyyy>/<mm>/<sha256[:2]>/<sha256>
"""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

from backend.config import get_settings
from backend.utils.hashing import sha256_file


def _store_root() -> Path:
    settings = get_settings()
    root = settings.storage_local_abs_path
    root.mkdir(parents=True, exist_ok=True)
    return root


def _hash_subpath(file_hash: str) -> str:
    now = datetime.now(timezone.utc)
    return f"{now:%Y}/{now:%m}/{file_hash[:2]}/{file_hash}"


def save_file(src: str | Path, *, mime_type: str | None = None) -> tuple[str, str, int]:
    """src 파일을 storage에 복사. 동일 hash가 이미 있으면 그대로 재사용.

    Returns: (file_path, file_hash, file_size)
    """
    src_path = Path(src)
    file_hash = sha256_file(src_path)
    dest = _store_root() / _hash_subpath(file_hash)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        shutil.copy2(src_path, dest)
    return (str(dest), file_hash, dest.stat().st_size)


def save_bytes(data: bytes) -> tuple[str, str, int]:
    """바이트 직접 저장. 같은 내용이면 재사용."""
    import hashlib
    file_hash = hashlib.sha256(data).hexdigest()
    dest = _store_root() / _hash_subpath(file_hash)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        dest.write_bytes(data)
    return (str(dest), file_hash, len(data))
