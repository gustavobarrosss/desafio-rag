"""Run the vision path for docs whose fast parse flagged pages needing vision."""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from tqdm import tqdm

from .download import _local_path
from .parse_digital import load_parsed
from .parse_vision import VisionClient, run_vision_for_doc
from .state import get_doc, pending

log = logging.getLogger(__name__)


def _needs_vision(doc_id: str) -> bool:
    parsed = load_parsed(doc_id)
    if not parsed:
        return False
    return any(p.get("needs_vision") for p in parsed["pages"])


async def run_vision_batch(limit: int | None = None) -> dict:
    from .state import connection
    with connection() as conn:
        rows = conn.execute(
            "SELECT * FROM docs WHERE status_parse='ok' AND status_download='ok' ORDER BY doc_id"
        ).fetchall()
    candidates: list[dict] = []
    for row in rows:
        doc_id = row["doc_id"]
        if _needs_vision(doc_id):
            candidates.append(dict(row))
    if limit:
        candidates = candidates[:limit]
    if not candidates:
        return {"candidates": 0, "ok": 0, "errors": 0, "pages": 0}

    client = VisionClient()
    ok = errors = 0
    pages = 0
    try:
        for row in tqdm(candidates, desc="vision"):
            doc_id = row["doc_id"]
            pdf_path = _local_path(row["arquivo"] or f"{doc_id}.pdf", doc_id)
            if not pdf_path.exists():
                errors += 1
                continue
            try:
                results = await run_vision_for_doc(client, doc_id, str(pdf_path))
                pages += sum(1 for r in results if r.status == "ok")
                ok += 1
            except Exception as exc:
                log.exception("vision failed for %s: %s", doc_id, exc)
                errors += 1
    finally:
        await client.close()
    return {"candidates": len(candidates), "ok": ok, "errors": errors, "pages": pages}


def main(limit: int | None = None) -> None:
    logging.basicConfig(level=logging.INFO)
    stats = asyncio.run(run_vision_batch(limit=limit))
    log.info("vision stats: %s", stats)


if __name__ == "__main__":
    import sys
    main(int(sys.argv[1]) if len(sys.argv) > 1 else None)
