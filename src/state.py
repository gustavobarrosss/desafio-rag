from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

from .config import STATE_DB, ensure_dirs

SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
    doc_id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    arquivo TEXT NOT NULL,
    ano INTEGER,
    tipo_pdf TEXT,
    titulo TEXT,
    autor TEXT,
    situacao_doc TEXT,
    assunto TEXT,
    ementa TEXT,
    assinatura TEXT,
    publicacao TEXT,
    data_bucket TEXT,
    metadata_json TEXT,
    status_download TEXT DEFAULT 'pending',
    status_parse TEXT DEFAULT 'pending',
    status_chunk TEXT DEFAULT 'pending',
    status_embed TEXT DEFAULT 'pending',
    parse_path_mix TEXT,
    bytes INTEGER,
    pages INTEGER,
    error TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_docs_download ON docs(status_download);
CREATE INDEX IF NOT EXISTS idx_docs_parse ON docs(status_parse);
CREATE INDEX IF NOT EXISTS idx_docs_chunk ON docs(status_chunk);
CREATE INDEX IF NOT EXISTS idx_docs_embed ON docs(status_embed);
CREATE INDEX IF NOT EXISTS idx_docs_ano ON docs(ano);
"""


def _connect() -> sqlite3.Connection:
    ensure_dirs()
    conn = sqlite3.connect(STATE_DB, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(SCHEMA)


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    conn = _connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def upsert_doc(record: dict[str, Any]) -> None:
    cols = [
        "doc_id", "url", "arquivo", "ano", "tipo_pdf", "titulo", "autor",
        "situacao_doc", "assunto", "ementa", "assinatura", "publicacao",
        "data_bucket", "metadata_json",
    ]
    placeholders = ",".join(["?"] * len(cols))
    assignments = ",".join(f"{c}=excluded.{c}" for c in cols if c != "doc_id")
    sql = (
        f"INSERT INTO docs ({','.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT(doc_id) DO UPDATE SET {assignments}, updated_at=CURRENT_TIMESTAMP"
    )
    with connection() as conn:
        conn.execute(sql, [record.get(c) for c in cols])


def bulk_upsert_docs(records: Iterable[dict[str, Any]]) -> int:
    cols = [
        "doc_id", "url", "arquivo", "ano", "tipo_pdf", "titulo", "autor",
        "situacao_doc", "assunto", "ementa", "assinatura", "publicacao",
        "data_bucket", "metadata_json",
    ]
    placeholders = ",".join(["?"] * len(cols))
    assignments = ",".join(f"{c}=excluded.{c}" for c in cols if c != "doc_id")
    sql = (
        f"INSERT INTO docs ({','.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT(doc_id) DO UPDATE SET {assignments}, updated_at=CURRENT_TIMESTAMP"
    )
    n = 0
    with connection() as conn:
        for rec in records:
            conn.execute(sql, [rec.get(c) for c in cols])
            n += 1
    return n


def mark_status(doc_id: str, stage: str, status: str, **extra: Any) -> None:
    col = f"status_{stage}"
    if col not in {"status_download", "status_parse", "status_chunk", "status_embed"}:
        raise ValueError(f"invalid stage: {stage}")
    fields = [f"{col}=?", "updated_at=CURRENT_TIMESTAMP"]
    vals: list[Any] = [status]
    for k, v in extra.items():
        fields.append(f"{k}=?")
        vals.append(v)
    vals.append(doc_id)
    with connection() as conn:
        conn.execute(f"UPDATE docs SET {','.join(fields)} WHERE doc_id=?", vals)


def pending(stage: str, limit: int | None = None) -> list[sqlite3.Row]:
    col = f"status_{stage}"
    sql = f"SELECT * FROM docs WHERE {col} IN ('pending', 'error')"
    if stage != "download":
        sql += " AND status_download='ok'"
    if stage == "chunk":
        sql += " AND status_parse='ok'"
    if stage == "embed":
        sql += " AND status_chunk='ok'"
    sql += " ORDER BY doc_id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    with connection() as conn:
        return list(conn.execute(sql).fetchall())


def counts() -> dict[str, dict[str, int]]:
    with connection() as conn:
        result: dict[str, dict[str, int]] = {}
        for stage in ("download", "parse", "chunk", "embed"):
            col = f"status_{stage}"
            rows = conn.execute(f"SELECT {col} AS s, COUNT(*) AS c FROM docs GROUP BY {col}").fetchall()
            result[stage] = {r["s"]: r["c"] for r in rows}
        return result


def get_doc(doc_id: str) -> sqlite3.Row | None:
    with connection() as conn:
        row = conn.execute("SELECT * FROM docs WHERE doc_id=?", (doc_id,)).fetchone()
        return row
