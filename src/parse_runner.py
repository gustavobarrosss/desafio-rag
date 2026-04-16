"""Orchestrates parsing: run fast path over all downloaded docs, mark status."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Sequence

from tqdm import tqdm

from .config import PDF_DIR
from .download import _local_path
from .parse_digital import ParsedDoc, parse_pdf, save_parsed, summary
from .state import mark_status, pending

log = logging.getLogger(__name__)


def _resolve_pdf_path(row: dict) -> Path | None:
    path = _local_path(row["arquivo"] or f"{row['doc_id']}.pdf", row["doc_id"])
    if path.exists() and path.stat().st_size > 0:
        return path
    candidates = list(PDF_DIR.glob(f"*{row['doc_id'][-10:]}*.pdf"))
    if candidates:
        return candidates[0]
    return None


def parse_batch(limit: int | None = None) -> dict:
    rows = pending("parse", limit=limit)
    if not rows:
        return {"pending": 0, "ok": 0, "errors": 0}
    ok = errors = 0
    mix_counts: dict[str, int] = {"digital": 0, "mixed": 0, "vision_heavy": 0}
    skipped = 0
    for row in tqdm(rows, desc="parse"):
        row_d = dict(row)
        pdf_path = _resolve_pdf_path(row_d)
        if not pdf_path:
            mark_status(row_d["doc_id"], "parse", "error", error="pdf not found")
            errors += 1
            continue
        with pdf_path.open("rb") as fp:
            head = fp.read(5)
        if head[:4] != b"%PDF":
            mark_status(row_d["doc_id"], "parse", "skipped", error="not_pdf")
            skipped += 1
            continue
        try:
            parsed = parse_pdf(row_d["doc_id"], str(pdf_path))
            save_parsed(parsed)
            sm = summary(parsed)
            if sm["needs_vision_pages"] == 0:
                mix = "digital"
            elif sm["needs_vision_pages"] < sm["pages"]:
                mix = "mixed"
            else:
                mix = "vision_heavy"
            mix_counts[mix] += 1
            mark_status(row_d["doc_id"], "parse", "ok",
                        parse_path_mix=mix, pages=sm["pages"])
            ok += 1
        except Exception as exc:
            log.exception("parse failed for %s", row_d["doc_id"])
            mark_status(row_d["doc_id"], "parse", "error", error=str(exc)[:240])
            errors += 1
    return {"pending": len(rows), "ok": ok, "errors": errors, "skipped": skipped, "mix": mix_counts}


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    n = int(sys.argv[1]) if len(sys.argv) > 1 else None
    print(parse_batch(limit=n))
