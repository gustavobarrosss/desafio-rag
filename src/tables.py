"""Table extraction with pdfplumber (primary) and Camelot (fallback). Serialize to markdown."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class ExtractedTable:
    page_index: int
    bbox: tuple[float, float, float, float] | None
    rows: list[list[str]]
    source: str

    def to_markdown(self) -> str:
        if not self.rows:
            return ""
        header = self.rows[0]
        body = self.rows[1:] if len(self.rows) > 1 else []
        ncols = max(len(r) for r in self.rows)
        header = list(header) + [""] * (ncols - len(header))
        sep = ["---"] * ncols
        lines = ["| " + " | ".join(_md_cell(c) for c in header) + " |",
                 "| " + " | ".join(sep) + " |"]
        for row in body:
            row = list(row) + [""] * (ncols - len(row))
            lines.append("| " + " | ".join(_md_cell(c) for c in row) + " |")
        return "\n".join(lines)


def _md_cell(val: Any) -> str:
    if val is None:
        return ""
    s = str(val).replace("\n", " ").replace("|", "\\|").strip()
    return s or ""


def extract_with_pdfplumber(pdf_path: str) -> list[ExtractedTable]:
    import pdfplumber

    tables: list[ExtractedTable] = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for i, page in enumerate(pdf.pages):
                try:
                    found = page.find_tables() or []
                except Exception as exc:
                    log.debug("pdfplumber find_tables failed p=%s: %s", i, exc)
                    continue
                for t in found:
                    try:
                        rows = t.extract()
                    except Exception:
                        rows = None
                    if not rows:
                        continue
                    rows = [[(c or "").strip() for c in row] for row in rows]
                    tables.append(ExtractedTable(
                        page_index=i,
                        bbox=tuple(t.bbox) if getattr(t, "bbox", None) else None,
                        rows=rows,
                        source="pdfplumber",
                    ))
    except Exception as exc:
        log.warning("pdfplumber failed on %s: %s", pdf_path, exc)
    return tables


def extract_with_camelot(pdf_path: str, pages: list[int] | None = None) -> list[ExtractedTable]:
    try:
        import camelot
    except Exception as exc:
        log.debug("camelot unavailable: %s", exc)
        return []

    pages_arg = ",".join(str(p + 1) for p in pages) if pages else "all"
    tables: list[ExtractedTable] = []
    for flavor in ("lattice", "stream"):
        try:
            found = camelot.read_pdf(pdf_path, pages=pages_arg, flavor=flavor, suppress_stdout=True)
        except Exception as exc:
            log.debug("camelot %s failed: %s", flavor, exc)
            continue
        for t in found:
            rows = t.df.fillna("").values.tolist()
            rows = [[str(c).strip() for c in row] for row in rows]
            tables.append(ExtractedTable(
                page_index=int(t.page) - 1,
                bbox=None,
                rows=rows,
                source=f"camelot-{flavor}",
            ))
        if tables:
            break
    return tables


def extract_all(pdf_path: str, fallback_pages: list[int] | None = None) -> list[ExtractedTable]:
    tables = extract_with_pdfplumber(pdf_path)
    if tables:
        return tables
    return extract_with_camelot(pdf_path, pages=fallback_pages)


def tables_for_page(tables: list[ExtractedTable], page_index: int) -> list[ExtractedTable]:
    return [t for t in tables if t.page_index == page_index]
