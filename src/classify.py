"""Per-page routing: digital (fast path) vs scanned/complex (vision path)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import fitz  # PyMuPDF

from .config import SETTINGS

PageClass = Literal["digital", "scanned", "complex"]


@dataclass
class PageSignal:
    page_index: int
    char_count: int
    image_area_ratio: float
    has_drawings: bool
    suspected_table: bool
    classification: PageClass


def _image_area_ratio(page: fitz.Page) -> float:
    try:
        page_area = page.rect.width * page.rect.height
        if page_area <= 0:
            return 0.0
        covered = 0.0
        for img in page.get_images(full=True):
            xref = img[0]
            for rect in page.get_image_rects(xref):
                covered += rect.width * rect.height
        return min(covered / page_area, 1.0)
    except Exception:
        return 0.0


def _suspected_table(page: fitz.Page) -> bool:
    try:
        tables = page.find_tables()
        return tables.tables is not None and len(tables.tables) > 0
    except Exception:
        return False


def classify_page(page: fitz.Page) -> PageSignal:
    text = page.get_text("text") or ""
    char_count = len(text.strip())
    img_ratio = _image_area_ratio(page)
    drawings = page.get_drawings()
    has_drawings = bool(drawings)
    suspected_table = _suspected_table(page)

    cfg = SETTINGS.router
    if char_count < cfg.min_chars_per_page and img_ratio >= cfg.image_area_ratio_threshold:
        cls: PageClass = "scanned"
    elif char_count < cfg.min_chars_per_page and img_ratio < cfg.image_area_ratio_threshold:
        cls = "scanned"
    elif suspected_table:
        cls = "complex"
    else:
        cls = "digital"
    return PageSignal(
        page_index=page.number,
        char_count=char_count,
        image_area_ratio=img_ratio,
        has_drawings=has_drawings,
        suspected_table=suspected_table,
        classification=cls,
    )


def classify_pdf(pdf_path: str) -> list[PageSignal]:
    signals: list[PageSignal] = []
    with fitz.open(pdf_path) as doc:
        for page in doc:
            signals.append(classify_page(page))
    return signals


def summarize(signals: list[PageSignal]) -> dict[str, int]:
    out = {"digital": 0, "scanned": 0, "complex": 0, "total": len(signals)}
    for s in signals:
        out[s.classification] += 1
    return out
