"""Chunk parsed documents along Brazilian legal structure (Art., §, Inciso, Capítulo)."""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

from .config import CHUNKS_DIR, SETTINGS, ensure_dirs
from .parse_digital import load_parsed
from .parse_vision import load_vision_results, merged_page_text
from .state import get_doc, mark_status, pending

log = logging.getLogger(__name__)

_HEADING_PATTERNS = [
    (re.compile(r"^\s*CAP[IÍ]TULO\s+[IVXLCDM]+", re.IGNORECASE), "capitulo"),
    (re.compile(r"^\s*SE[ÇC][AÃ]O\s+[IVXLCDM]+", re.IGNORECASE), "secao"),
    (re.compile(r"^\s*TÍTULO\s+[IVXLCDM]+", re.IGNORECASE), "titulo"),
    (re.compile(r"^\s*Art\.\s*\d+[ºo]?\b", re.IGNORECASE), "artigo"),
    (re.compile(r"^\s*§\s*\d+[ºo]?\b"), "paragrafo"),
    (re.compile(r"^\s*Par[áa]grafo\s+[úu]nico\b", re.IGNORECASE), "paragrafo"),
]

_REVOKED_MARKER = re.compile(r"~~([^~]+)~~")


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    text: str
    article_ref: str | None
    has_table: bool
    has_revoked: bool
    page_start: int
    page_end: int
    metadata: dict = field(default_factory=dict)

    def to_payload(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "doc_id": self.doc_id,
            "text": self.text,
            "article_ref": self.article_ref,
            "has_table": self.has_table,
            "has_revoked": self.has_revoked,
            "page_start": self.page_start,
            "page_end": self.page_end,
            **self.metadata,
        }


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _is_heading(line: str) -> tuple[str | None, str | None]:
    for pat, kind in _HEADING_PATTERNS:
        if pat.match(line):
            return kind, line.strip()
    return None, None


def _split_by_structure(pages_text: list[tuple[int, str]]) -> list[dict]:
    """Yield blocks: each has `text`, `page_start`, `page_end`, `article_ref`."""
    blocks: list[dict] = []
    current: dict | None = None
    current_article: str | None = None

    def new_block(page: int, article: str | None) -> dict:
        return {"lines": [], "page_start": page, "page_end": page, "article_ref": article}

    for page_idx, text in pages_text:
        for raw in text.splitlines():
            line = raw.rstrip()
            if not line.strip():
                if current is not None:
                    current["lines"].append("")
                continue
            kind, heading_text = _is_heading(line)
            if kind == "artigo":
                if current and current["lines"]:
                    blocks.append(current)
                current_article = heading_text
                current = new_block(page_idx, current_article)
                current["lines"].append(line)
            elif kind in {"capitulo", "secao", "titulo"}:
                if current and current["lines"]:
                    blocks.append(current)
                current = new_block(page_idx, current_article)
                current["lines"].append(line)
            else:
                if current is None:
                    current = new_block(page_idx, current_article)
                current["lines"].append(line)
                current["page_end"] = page_idx
    if current and current["lines"]:
        blocks.append(current)

    for b in blocks:
        b["text"] = "\n".join(b["lines"]).strip()
        b.pop("lines", None)
    return [b for b in blocks if b["text"]]


def _sliding_window(text: str, max_tokens: int, overlap_tokens: int) -> list[str]:
    tokens_per_char = 0.25
    window_chars = int(max_tokens / tokens_per_char)
    overlap_chars = int(overlap_tokens / tokens_per_char)
    paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        return [text]
    out: list[str] = []
    buf: list[str] = []
    buf_len = 0
    for para in paragraphs:
        plen = len(para)
        if buf_len + plen > window_chars and buf:
            out.append("\n\n".join(buf))
            keep: list[str] = []
            keep_len = 0
            for pp in reversed(buf):
                if keep_len + len(pp) > overlap_chars:
                    break
                keep.insert(0, pp)
                keep_len += len(pp)
            buf = keep
            buf_len = sum(len(p) for p in buf)
        buf.append(para)
        buf_len += plen
    if buf:
        out.append("\n\n".join(buf))
    return out


def _chunk_id(doc_id: str, idx: int, text: str) -> str:
    h = hashlib.sha1(f"{doc_id}|{idx}|{text[:200]}".encode("utf-8")).hexdigest()[:16]
    return f"{doc_id}::{idx:04d}::{h}"


def _has_table(text: str) -> bool:
    return bool(re.search(r"\n\s*\|.+\|\s*\n\s*\|\s*-{2,}", text))


def _has_revoked(text: str) -> bool:
    return bool(_REVOKED_MARKER.search(text))


def chunk_doc(doc_id: str) -> list[Chunk]:
    parsed = load_parsed(doc_id)
    if not parsed:
        return []
    vision = load_vision_results(doc_id)
    pages_text: list[tuple[int, str]] = []
    for p in parsed["pages"]:
        merged = merged_page_text(p, vision).strip()
        if merged:
            pages_text.append((p["page_index"], merged))
    blocks = _split_by_structure(pages_text)
    cfg = SETTINGS.chunker

    row = get_doc(doc_id)
    meta: dict = {}
    if row is not None:
        meta = {
            "ano": row["ano"],
            "url": row["url"],
            "arquivo": row["arquivo"],
            "tipo_pdf": row["tipo_pdf"],
            "titulo": row["titulo"],
            "autor": row["autor"],
            "situacao_doc": row["situacao_doc"],
            "assunto": row["assunto"],
            "ementa": row["ementa"],
            "assinatura": row["assinatura"],
            "publicacao": row["publicacao"],
        }

    out: list[Chunk] = []
    idx = 0
    for b in blocks:
        text = b["text"]
        if _approx_tokens(text) > cfg.max_tokens:
            pieces = _sliding_window(text, cfg.max_tokens, cfg.overlap_tokens)
        else:
            pieces = [text]
        for piece in pieces:
            if _approx_tokens(piece) < cfg.min_tokens and out and not _has_table(piece):
                out[-1].text = out[-1].text + "\n\n" + piece
                out[-1].page_end = b["page_end"]
                out[-1].has_revoked = out[-1].has_revoked or _has_revoked(piece)
                continue
            chunk = Chunk(
                chunk_id=_chunk_id(doc_id, idx, piece),
                doc_id=doc_id,
                text=piece,
                article_ref=b.get("article_ref"),
                has_table=_has_table(piece),
                has_revoked=_has_revoked(piece),
                page_start=b["page_start"],
                page_end=b["page_end"],
                metadata=meta,
            )
            out.append(chunk)
            idx += 1
    return out


def _chunks_path(doc_id: str) -> Path:
    return CHUNKS_DIR / f"{doc_id}.jsonl"


def save_chunks(doc_id: str, chunks: list[Chunk]) -> Path:
    ensure_dirs()
    path = _chunks_path(doc_id)
    with path.open("w", encoding="utf-8") as fp:
        for c in chunks:
            fp.write(json.dumps(c.to_payload(), ensure_ascii=False) + "\n")
    return path


def load_chunks(doc_id: str) -> list[dict]:
    path = _chunks_path(doc_id)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def chunk_batch(limit: int | None = None) -> dict:
    rows = pending("chunk", limit=limit)
    ok = errors = 0
    total_chunks = 0
    for row in rows:
        doc_id = row["doc_id"]
        try:
            chunks = chunk_doc(doc_id)
            save_chunks(doc_id, chunks)
            mark_status(doc_id, "chunk", "ok")
            ok += 1
            total_chunks += len(chunks)
        except Exception as exc:
            log.exception("chunking failed for %s", doc_id)
            mark_status(doc_id, "chunk", "error", error=str(exc)[:240])
            errors += 1
    return {"pending": len(rows), "ok": ok, "errors": errors, "chunks": total_chunks}


def iter_all_chunks() -> Iterator[dict]:
    for path in CHUNKS_DIR.glob("*.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                yield json.loads(line)
