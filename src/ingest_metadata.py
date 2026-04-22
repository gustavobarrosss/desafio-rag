"""Load ANEEL metadata JSONs into the state DB as `docs` rows."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterator

from .config import METADATA_FILES, bootstrap_metadata
from .state import bulk_upsert_docs, init_db


_YEAR_RE = re.compile(r"(\d{4})_metadados\.json$")


def _year_of(path: Path) -> int | None:
    m = _YEAR_RE.search(path.name)
    return int(m.group(1)) if m else None


def _clean(value: str | None, prefix: str | None = None) -> str | None:
    if not value:
        return None
    text = value.strip()
    if prefix and text.lower().startswith(prefix.lower()):
        text = text[len(prefix):].strip()
    return text or None


def _doc_id(arquivo: str, url: str) -> str:
    base = arquivo.strip() or url.strip()
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
    return f"{Path(base).stem}__{digest}"


def iter_records(meta_files: list[Path] | None = None) -> Iterator[dict[str, Any]]:
    for path in meta_files or METADATA_FILES:
        if not path.exists():
            continue
        ano = _year_of(path)
        with path.open("r", encoding="utf-8") as fp:
            data = json.load(fp)
        for bucket, payload in data.items():
            for registro in payload.get("registros", []):
                base = {
                    "ano": ano,
                    "data_bucket": bucket,
                    "titulo": registro.get("titulo"),
                    "autor": registro.get("autor"),
                    "situacao_doc": _clean(registro.get("situacao"), "Situação:"),
                    "assunto": _clean(registro.get("assunto"), "Assunto:"),
                    "ementa": registro.get("ementa"),
                    "assinatura": _clean(registro.get("assinatura"), "Assinatura:"),
                    "publicacao": _clean(registro.get("publicacao"), "Publicação:"),
                }
                for pdf in registro.get("pdfs", []) or []:
                    url = pdf.get("url")
                    arquivo = pdf.get("arquivo") or ""
                    if not url:
                        continue
                    if not url.lower().split("?")[0].endswith(".pdf"):
                        continue
                    yield {
                        **base,
                        "doc_id": _doc_id(arquivo, url),
                        "url": url,
                        "arquivo": arquivo,
                        "tipo_pdf": (pdf.get("tipo") or "").strip().rstrip(":").strip(),
                        "metadata_json": json.dumps(registro, ensure_ascii=False),
                    }


def load_all() -> int:
    init_db()
    bootstrap_metadata()
    return bulk_upsert_docs(iter_records())


if __name__ == "__main__":
    n = load_all()
    print(f"loaded {n} doc rows into state.sqlite")
