"""Fast-path PDF parser: text with strikethrough + tables as markdown."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

import fitz

from .classify import PageSignal, classify_pdf, classify_page
from .config import PARSED_DIR, ensure_dirs
from .strikethrough import annotate_spans, any_revoked, spans_to_markdown
from .tables import ExtractedTable, extract_all, tables_for_page

log = logging.getLogger(__name__)


@dataclass
class ParsedPage:
    page_index: int
    classification: str
    text_markdown: str
    tables_markdown: list[str]
    has_revoked: bool
    char_count: int
    needs_vision: bool


@dataclass
class ParsedDoc:
    doc_id: str
    pdf_path: str
    pages: list[ParsedPage]

    def to_json(self) -> dict:
        return {
            "doc_id": self.doc_id,
            "pdf_path": self.pdf_path,
            "pages": [asdict(p) for p in self.pages],
        }


def parse_digital_page(page: fitz.Page, signal: PageSignal,
                       page_tables: list[ExtractedTable]) -> ParsedPage:
    lines = annotate_spans(page)
    text_md = spans_to_markdown(lines)
    revoked = any_revoked(lines)
    tables_md = [t.to_markdown() for t in page_tables if t.rows]
    needs_vision = signal.classification in {"scanned", "complex"} and not text_md.strip()
    if signal.classification == "complex" and not tables_md:
        needs_vision = True
    return ParsedPage(
        page_index=signal.page_index,
        classification=signal.classification,
        text_markdown=text_md,
        tables_markdown=tables_md,
        has_revoked=revoked,
        char_count=signal.char_count,
        needs_vision=needs_vision,
    )


def parse_pdf(doc_id: str, pdf_path: str) -> ParsedDoc:
    signals = classify_pdf(pdf_path)
    tables = extract_all(pdf_path)
    pages: list[ParsedPage] = []
    with fitz.open(pdf_path) as doc:
        for page in doc:
            sig = signals[page.number]
            pt = tables_for_page(tables, page.number)
            pages.append(parse_digital_page(page, sig, pt))
    return ParsedDoc(doc_id=doc_id, pdf_path=str(pdf_path), pages=pages)


def _parsed_path(doc_id: str) -> Path:
    return PARSED_DIR / f"{doc_id}.json"


def save_parsed(parsed: ParsedDoc) -> Path:
    ensure_dirs()
    path = _parsed_path(parsed.doc_id)
    path.write_text(json.dumps(parsed.to_json(), ensure_ascii=False), encoding="utf-8")
    return path


def load_parsed(doc_id: str) -> Optional[dict]:
    path = _parsed_path(doc_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def summary(parsed: ParsedDoc) -> dict:
    total = len(parsed.pages)
    scanned = sum(1 for p in parsed.pages if p.classification == "scanned")
    complex_pages = sum(1 for p in parsed.pages if p.classification == "complex")
    revoked = sum(1 for p in parsed.pages if p.has_revoked)
    need_vision = sum(1 for p in parsed.pages if p.needs_vision)
    chars = sum(p.char_count for p in parsed.pages)
    return {
        "pages": total,
        "scanned": scanned,
        "complex": complex_pages,
        "revoked_pages": revoked,
        "needs_vision_pages": need_vision,
        "total_chars": chars,
    }
