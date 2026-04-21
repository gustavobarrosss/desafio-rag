"""Orchestrates parsing: run fast path over all downloaded docs, mark status."""
from __future__ import annotations

import logging
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path

from tqdm import tqdm

from .config import PDF_DIR
from .download import _local_path
from .parse_digital import parse_pdf, save_parsed, summary
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


def _worker(doc_id: str, pdf_path: str) -> dict:
    """Pure worker: parses + saves JSON. No DB writes."""
    try:
        parsed = parse_pdf(doc_id, pdf_path)
        save_parsed(parsed)
        sm = summary(parsed)
        if sm["needs_vision_pages"] == 0:
            mix = "digital"
        elif sm["needs_vision_pages"] < sm["pages"]:
            mix = "mixed"
        else:
            mix = "vision_heavy"
        return {"doc_id": doc_id, "ok": True, "mix": mix, "pages": sm["pages"]}
    except Exception as exc:
        return {"doc_id": doc_id, "ok": False, "error": str(exc)[:240]}


def parse_batch(limit: int | None = None, workers: int | None = None) -> dict:
    rows = pending("parse", limit=limit)
    if not rows:
        return {"pending": 0, "ok": 0, "errors": 0}

    if workers is None:
        workers = int(os.environ.get("PARSE_WORKERS", "6"))

    ok = errors = skipped = 0
    mix_counts: dict[str, int] = {"digital": 0, "mixed": 0, "vision_heavy": 0}

    jobs: list[tuple[str, str]] = []
    for row in rows:
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
        jobs.append((row_d["doc_id"], str(pdf_path)))

    if not jobs:
        return {"pending": len(rows), "ok": ok, "errors": errors, "skipped": skipped,
                "mix": mix_counts}

    crashed = 0
    remaining = list(jobs)
    with tqdm(total=len(jobs), desc="parse") as pbar:
        while remaining:
            done_ids: set[str] = set()
            pool_died = False
            try:
                with ProcessPoolExecutor(max_workers=workers) as ex:
                    futures = {ex.submit(_worker, doc_id, pdf_path): doc_id
                               for doc_id, pdf_path in remaining}
                    for fut in as_completed(futures):
                        try:
                            res = fut.result()
                        except BrokenProcessPool:
                            pool_died = True
                            break
                        doc_id = res["doc_id"]
                        done_ids.add(doc_id)
                        if res["ok"]:
                            mix_counts[res["mix"]] += 1
                            mark_status(doc_id, "parse", "ok",
                                        parse_path_mix=res["mix"], pages=res["pages"])
                            ok += 1
                        else:
                            log.error("parse failed for %s: %s", doc_id, res["error"])
                            mark_status(doc_id, "parse", "error", error=res["error"])
                            errors += 1
                        pbar.update(1)
            except BrokenProcessPool:
                pool_died = True

            remaining = [(d, p) for d, p in remaining if d not in done_ids]
            if not pool_died:
                break
            if remaining:
                suspect_id, suspect_path = remaining[0]
                log.warning("worker pool crashed; marking suspect %s as error and continuing",
                            suspect_id)
                mark_status(suspect_id, "parse", "error",
                            error=f"worker crashed on {Path(suspect_path).name}")
                errors += 1
                crashed += 1
                remaining = remaining[1:]
                pbar.update(1)

    return {"pending": len(rows), "ok": ok, "errors": errors, "skipped": skipped,
            "crashed": crashed, "mix": mix_counts, "workers": workers}


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    n = int(sys.argv[1]) if len(sys.argv) > 1 else None
    print(parse_batch(limit=n))
