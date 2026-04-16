"""Async downloader for ANEEL PDFs with per-URL checkpointing."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Sequence

import httpx
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter
from tqdm.asyncio import tqdm as tqdm_async

from .config import PDF_DIR, SETTINGS, ensure_dirs
from .state import mark_status, pending

log = logging.getLogger(__name__)

_TRANSIENT = (httpx.TransportError, httpx.HTTPStatusError, httpx.TimeoutException)


def _local_path(arquivo: str, doc_id: str) -> Path:
    safe = arquivo.strip() or f"{doc_id}.pdf"
    safe = safe.replace("/", "_").replace("\\", "_")
    if not safe.lower().endswith(".pdf"):
        safe = f"{safe}.pdf"
    path = PDF_DIR / safe
    suffix = doc_id[-8:]
    stamped = PDF_DIR / f"{path.stem}__{suffix}_1.pdf"
    if stamped.exists():
        return stamped
    if path.exists():
        return path
    for i in range(1, 20):
        alt = PDF_DIR / f"{path.stem}__{suffix}_{i}.pdf"
        if not alt.exists():
            return alt
    return path


async def _download_one(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    row: dict,
) -> tuple[str, str, str | None, int | None]:
    doc_id = row["doc_id"]
    url = row["url"]
    arquivo = row["arquivo"] or f"{doc_id}.pdf"
    dest = _local_path(arquivo, doc_id)

    if dest.exists() and dest.stat().st_size > 0:
        return doc_id, "ok", str(dest), dest.stat().st_size

    async with sem:
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(SETTINGS.download.max_retries),
                wait=wait_exponential_jitter(initial=1.5, max=20),
                retry=retry_if_exception_type(_TRANSIENT),
                reraise=True,
            ):
                with attempt:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    content = resp.content
            tmp = dest.with_suffix(dest.suffix + ".part")
            tmp.write_bytes(content)
            tmp.replace(dest)
            return doc_id, "ok", str(dest), len(content)
        except Exception as exc:
            return doc_id, "error", None, None if str(exc) else 0


async def run_downloads(limit: int | None = None) -> dict:
    ensure_dirs()
    rows = pending("download", limit=limit)
    if not rows:
        return {"pending": 0, "done": 0, "errors": 0}

    headers = {"User-Agent": SETTINGS.download.user_agent, "Accept": "application/pdf,*/*"}
    timeout = httpx.Timeout(SETTINGS.download.timeout_s)
    sem = asyncio.Semaphore(SETTINGS.download.concurrency)

    done = errors = 0
    async with httpx.AsyncClient(headers=headers, timeout=timeout, follow_redirects=True) as client:
        tasks = [
            asyncio.create_task(_download_one(client, sem, dict(r)))
            for r in rows
        ]
        for coro in tqdm_async.as_completed(tasks, total=len(tasks), desc="download"):
            doc_id, status, path, size = await coro
            if status == "ok":
                mark_status(doc_id, "download", "ok", bytes=size)
                done += 1
            else:
                mark_status(doc_id, "download", "error", error="download failed")
                errors += 1
    return {"pending": len(rows), "done": done, "errors": errors}


def main(limit: int | None = None) -> None:
    logging.basicConfig(level=logging.INFO)
    stats = asyncio.run(run_downloads(limit=limit))
    log.info("download stats: %s", stats)


if __name__ == "__main__":
    import sys
    main(int(sys.argv[1]) if len(sys.argv) > 1 else None)
