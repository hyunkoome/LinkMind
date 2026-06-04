"""
backend.jobs.docling_batch — 미처리 PDF 를 배치로 Docling GPU 변환 (batch swap).

GPU 공존 불가(Gemma AWQ weight 16GB, RTX 4090 24GB)라 swap 으로 처리:
  vLLM 내림 → Docling GPU(편당 ~4s, CPU 48s 대비 11배) 대량 변환 → vLLM 복귀.
swap 1회 비용(~80s 재로드)을 수백 편으로 amortize. 배치 중 vLLM(ask/summary) 중단
되므로 야간/유휴 시간 권장.

처리 내용 (편당):
  - Docling GPU 변환 → 풍부한 markdown 으로 raw_content 교체 + extractor='docling' 마킹
  - 기존 chunk(Postgres+Qdrant) 삭제 후 재임베딩 (raw 가 바뀌었으므로)
  - figure 이미지 + caption → attachments(role='figure') 저장 (위키 본문 삽입의 소스)
  - summary=NULL 로 비워 vLLM 복귀 후 analysis_worker daemon 이 재생성하게 함
    (summary 는 LLM=vLLM 필요한데 배치 중엔 내려가 있으므로 여기서 안 만든다)

  python -m backend.jobs.docling_batch --limit 50         # 미처리 PDF 50편 (vLLM swap)
  python -m backend.jobs.docling_batch --dry-run          # 큐만 표시 (변환 안 함)
  python -m backend.jobs.docling_batch --limit 5 --keep-vllm   # vLLM 안 내림(소량/디버그)

관련: memory project_docling_vram_batch_swap.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import subprocess
import time
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.config import get_settings
from backend.db.connection import get_engine
from backend.embedding.qdrant_store import delete_chunks_for_item
from backend.ingest.docling_convert import convert_document, save_docling_figures
from backend.ingest.url import _embed_and_index
from backend.utils.hashing import sha256_text

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("linkmind.jobs.docling_batch")

# docker compose 호출 (프로젝트 루트에서 python -m 으로 실행되는 전제 — cwd=루트).
_COMPOSE = [
    "docker", "compose", "--env-file", "env/dev.env",
    "-f", "compose/docker-compose.dev.yml",
]

# 큐: Docling 으로 아직 처리 안 된 PDF (extractor != 'docling') + storage 경로 있는 것.
_QUEUE_SQL = text("""
    SELECT id,
           source_metadata->>'file_path' AS file_path
    FROM items
    WHERE source_type = 'pdf'
      AND COALESCE(source_metadata->>'extractor', '') <> 'docling'
      AND source_metadata->>'file_path' IS NOT NULL
    ORDER BY ingested_at DESC
    LIMIT :limit
""")

# raw 교체 + extractor 마킹 + summary 비움(daemon 재생성 트리거).
_UPDATE_RAW_SQL = text("""
    UPDATE items
    SET raw_content = :raw,
        raw_content_hash = :hash,
        summary = NULL,
        source_metadata = jsonb_set(
            COALESCE(source_metadata, '{}'::jsonb), '{extractor}', '"docling"'
        )
    WHERE id = :id
""")

_DELETE_CHUNKS_SQL = text("DELETE FROM chunks WHERE item_id = :id")


def _vllm_stop() -> None:
    logger.info("vLLM 내림 (GPU 비우기) ...")
    subprocess.run([*_COMPOSE, "stop", "vllm"], check=True)


def _vllm_start() -> None:
    logger.info("vLLM 복귀 (재구동, 모델 로딩 ~80s) ...")
    subprocess.run(["bash", "scripts/vllm_restart.sh"], check=True)


async def _process_one(
    session: AsyncSession, *, item_id, file_path: str, device: str,
) -> dict:
    """PDF 1편 Docling 변환 → raw 교체 + 재임베딩 + figure 저장."""
    doc = await convert_document(file_path, device=device, do_ocr=False)
    md = (doc.markdown or "").strip()
    if not md:
        return {"item_id": str(item_id), "ok": False, "reason": "빈 markdown"}

    # raw 가 바뀌므로 기존 chunk 제거 (Postgres + Qdrant) 후 재임베딩.
    await delete_chunks_for_item(str(item_id))
    await session.execute(_DELETE_CHUNKS_SQL, {"id": str(item_id)})
    await session.execute(_UPDATE_RAW_SQL, {
        "raw": md, "hash": sha256_text(md), "id": str(item_id),
    })
    await session.commit()

    n_chunks = await _embed_and_index(session, item_id=item_id, text=md)
    n_fig = await save_docling_figures(session, item_id=item_id, figures=doc.figures)
    await session.commit()
    return {
        "item_id": str(item_id), "ok": True,
        "chunks": n_chunks, "figures": n_fig, "md_len": len(md),
    }


async def main(limit: int, dry_run: bool, keep_vllm: bool) -> None:
    settings = get_settings()
    # swap 모드면 GPU(cuda), keep-vllm(디버그)면 설정 device(기본 cpu).
    device = settings.docling_device if keep_vllm else "cuda"

    engine = get_engine()
    SM = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with SM() as s:
        rows = (await s.execute(_QUEUE_SQL, {"limit": limit})).mappings().all()
    queue = [(r["id"], r["file_path"]) for r in rows if r["file_path"]]
    logger.info("큐: Docling 미처리 PDF %d편 (device=%s)", len(queue), device)

    if dry_run or not queue:
        for iid, fp in queue[:20]:
            logger.info("  %s  %s", iid, fp)
        if dry_run:
            logger.info("[dry-run] 변환 안 함.")
        return

    vllm_down = False
    if not keep_vllm:
        _vllm_stop()
        vllm_down = True
        time.sleep(5)
    try:
        ok = 0
        t0 = time.time()
        for iid, fp in queue:
            if not Path(fp).exists():
                logger.warning("파일 없음 skip: %s", fp)
                continue
            async with SM() as s:
                try:
                    r = await _process_one(s, item_id=iid, file_path=fp, device=device)
                except Exception as e:  # noqa: BLE001 — 한 편 실패가 배치 전체를 막지 않게
                    logger.error("❌ %s 변환 실패: %s", iid, e)
                    continue
            if r["ok"]:
                ok += 1
                logger.info(
                    "✅ %s chunks=%d figures=%d md=%d자", iid,
                    r["chunks"], r["figures"], r["md_len"],
                )
            else:
                logger.warning("⚠️ %s %s", iid, r["reason"])
        dt = time.time() - t0
        logger.info("완료 %d/%d편 (%.1fs, 편당 %.1fs)",
                    ok, len(queue), dt, dt / max(ok, 1))
    finally:
        # 배치 도중 무엇이 터져도 vLLM 은 반드시 복귀시킨다 (ask 중단 방지).
        if vllm_down:
            _vllm_start()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--keep-vllm", action="store_true",
                    help="vLLM 안 내림 (소량/디버그, device=설정값)")
    args = ap.parse_args()
    asyncio.run(main(args.limit, args.dry_run, args.keep_vllm))
