"""Detect strikethrough text in digital PDFs using PyMuPDF drawings + annotations."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import fitz

_MIN_COVERAGE = 0.55
_VERTICAL_BAND = 0.45  # line must fall within the middle 45% of span height


@dataclass
class Span:
    text: str
    bbox: tuple[float, float, float, float]
    font: str
    size: float
    flags: int
    revoked: bool = False


def _extract_horizontal_lines(page: fitz.Page) -> list[tuple[float, float, float, float]]:
    lines: list[tuple[float, float, float, float]] = []
    try:
        drawings = page.get_drawings()
    except Exception:
        return lines
    for d in drawings:
        for item in d.get("items", []):
            kind = item[0]
            if kind == "l":
                (x0, y0), (x1, y1) = item[1], item[2]
                if abs(y1 - y0) <= 1.2 and abs(x1 - x0) > 4:
                    lines.append((min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))
            elif kind == "re":
                rect = item[1]
                h = rect.y1 - rect.y0
                w = rect.x1 - rect.x0
                if h <= 2.0 and w > 4:
                    lines.append((rect.x0, rect.y0, rect.x1, rect.y1))
    return lines


def _annot_strike_rects(page: fitz.Page) -> list[tuple[float, float, float, float]]:
    rects: list[tuple[float, float, float, float]] = []
    try:
        annot = page.first_annot
    except Exception:
        return rects
    while annot is not None:
        try:
            subtype = annot.type[1] if isinstance(annot.type, tuple) else str(annot.type)
        except Exception:
            subtype = ""
        if "Strike" in subtype:
            r = annot.rect
            rects.append((r.x0, r.y0, r.x1, r.y1))
        annot = annot.next
    return rects


def _line_strikes_span(span_bbox: tuple[float, float, float, float],
                       line: tuple[float, float, float, float]) -> bool:
    sx0, sy0, sx1, sy1 = span_bbox
    lx0, ly0, lx1, ly1 = line
    span_width = sx1 - sx0
    if span_width <= 0:
        return False
    mid = (sy0 + sy1) / 2
    band = (sy1 - sy0) * _VERTICAL_BAND
    if not (mid - band <= (ly0 + ly1) / 2 <= mid + band):
        return False
    overlap = max(0.0, min(sx1, lx1) - max(sx0, lx0))
    return overlap / span_width >= _MIN_COVERAGE


def _rect_overlaps_span(span_bbox, rect) -> bool:
    sx0, sy0, sx1, sy1 = span_bbox
    rx0, ry0, rx1, ry1 = rect
    if rx1 < sx0 or rx0 > sx1:
        return False
    if ry1 < sy0 or ry0 > sy1:
        return False
    overlap_x = max(0.0, min(sx1, rx1) - max(sx0, rx0))
    return overlap_x / max(1e-6, sx1 - sx0) >= _MIN_COVERAGE


def annotate_spans(page: fitz.Page) -> list[list[Span]]:
    """Return text blocks→lines→spans with `revoked` flag set."""
    raw = page.get_text("dict")
    lines = _extract_horizontal_lines(page)
    annot_rects = _annot_strike_rects(page)

    result: list[list[Span]] = []
    for block in raw.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans: list[Span] = []
            for span in line.get("spans", []):
                bbox = tuple(span["bbox"])
                text = span.get("text", "")
                revoked_font = False  # PyMuPDF flag 16 = bold, not strikethrough
                revoked_draw = any(_line_strikes_span(bbox, l) for l in lines)
                revoked_annot = any(_rect_overlaps_span(bbox, r) for r in annot_rects)
                spans.append(Span(
                    text=text,
                    bbox=bbox,
                    font=span.get("font", ""),
                    size=span.get("size", 0.0),
                    flags=span.get("flags", 0),
                    revoked=revoked_font or revoked_draw or revoked_annot,
                ))
            if spans:
                result.append(spans)
    return result


def spans_to_markdown(lines_spans: Iterable[list[Span]]) -> str:
    """Serialize spans to markdown, merging adjacent revoked runs and preserving line breaks."""
    out_lines: list[str] = []
    for spans in lines_spans:
        pieces: list[str] = []
        current_revoked = False
        buffer: list[str] = []

        def flush() -> None:
            if not buffer:
                return
            text = "".join(buffer)
            if current_revoked and len(text.strip()) >= 3:
                pieces.append(f"~~{text}~~")
            else:
                pieces.append(text)
            buffer.clear()

        for span in spans:
            if span.revoked != current_revoked:
                flush()
                current_revoked = span.revoked
            buffer.append(span.text)
        flush()
        line_text = "".join(pieces).rstrip()
        if line_text:
            out_lines.append(line_text)
    return "\n".join(out_lines)


def any_revoked(lines_spans: Iterable[list[Span]]) -> bool:
    return any(span.revoked for spans in lines_spans for span in spans)
