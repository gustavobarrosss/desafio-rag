"""
Test 10 selected documents covering:
  - Fully scanned PDFs (vision_heavy)
  - Mixed PDFs (digital + scanned pages)
  - Pure digital PDFs (non-scanned)
  - PDFs with tables
  - Revoked laws (REVOGADA metadata + strikethrough text)
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# --- Document selection ---
# Format: (doc_id, label)
TEST_DOCS = [
    # Fully scanned (vision_heavy)
    ("Pleito_CERAL-DIS%202016__7e796d8fc3",   "scanned / REH 2112/2016"),
    ("Pleito_Cermiss%C3%B5es%202016__936e3987e7", "scanned / REH 2116/2016"),
    # Mixed (digital + scanned pages + tables)
    ("aaap2022019_1__6d0517277e",              "mixed+table / AAP 019/2022"),
    ("aaap2022018_1__9b5eba0f18",              "mixed+table / AAP 018/2022"),
    # Revoked (situacao_doc=REVOGADA)
    ("Decis%C3%A3o_Judicial__572bf2e58b",      "REVOGADA / DSP 3407/2016"),
    # Digital + large + complex/tables
    ("aaap2022017_1__6161f9454c",              "digital+table large / AAP 017/2022"),
    # Digital + small
    ("Ndsp2016726__b9a27fab6d",               "digital+table small / DSP 726/2016"),
    # Digital + tables, no vision needed
    ("aacp2022035_1__fdf394550b",              "digital+table / ACP 035/2022"),
    # Digital + tables, larger
    ("aaap2022020_1__42466dfd93",              "digital+table large / AAP 020/2022"),
    # Digital, no strikethrough revoked
    ("Pleito_Coprel%202016__1c48f04733",       "digital / REH 2113/2016"),
]

PARSED_DIR = ROOT / "data" / "parsed"
CHUNKS_DIR = ROOT / "data" / "chunks"
STATE_DB   = ROOT / "data" / "state.sqlite"


# ── helpers ────────────────────────────────────────────────────────────────

def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(STATE_DB))
    conn.row_factory = sqlite3.Row
    return conn


def load_parsed(doc_id: str) -> dict | None:
    p = PARSED_DIR / f"{doc_id}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def load_chunks(doc_id: str) -> list[dict]:
    p = CHUNKS_DIR / f"{doc_id}.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def qdrant_count(doc_id: str) -> int:
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.models import Filter, FieldCondition, MatchValue
        from src.config import SETTINGS
        client = QdrantClient(url=SETTINGS.qdrant.url, timeout=5)
        r = client.count(
            collection_name=SETTINGS.qdrant.collection,
            count_filter=Filter(must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))]),
            exact=True,
        )
        return r.count
    except Exception:
        return -1  # Qdrant not available


# ── per-doc analysis ────────────────────────────────────────────────────────

def analyse_doc(doc_id: str, label: str, conn: sqlite3.Connection) -> dict:
    row = conn.execute("SELECT * FROM docs WHERE doc_id=?", (doc_id,)).fetchone()
    parsed = load_parsed(doc_id)
    chunks = load_chunks(doc_id)
    vecs = qdrant_count(doc_id)

    result: dict = {
        "doc_id": doc_id,
        "label": label,
        "status_download": row["status_download"] if row else "NOT IN DB",
        "status_parse":    row["status_parse"]    if row else "NOT IN DB",
        "status_chunk":    row["status_chunk"]    if row else "NOT IN DB",
        "status_embed":    row["status_embed"]    if row else "NOT IN DB",
        "situacao_doc":    row["situacao_doc"]    if row else None,
        "parse_path_mix":  row["parse_path_mix"]  if row else None,
    }

    if parsed:
        pages = parsed["pages"]
        result["pages_total"]   = len(pages)
        result["pages_scanned"] = sum(1 for p in pages if p["classification"] == "scanned")
        result["pages_complex"] = sum(1 for p in pages if p["classification"] == "complex")
        result["pages_digital"] = sum(1 for p in pages if p["classification"] == "digital")
        result["pages_vision"]  = sum(1 for p in pages if p["needs_vision"])
        result["has_table"]     = any(p["tables_markdown"] for p in pages)
        result["has_revoked_text"] = any(p["has_revoked"] for p in pages)
        result["tables_count"]  = sum(len(p["tables_markdown"]) for p in pages)
        result["total_chars"]   = sum(p["char_count"] for p in pages)

        # sample first table
        for p in pages:
            if p["tables_markdown"]:
                result["table_sample"] = p["tables_markdown"][0][:200].replace("\n", " | ")
                break

        # sample revoked snippet
        import re
        _REV = re.compile(r"~~([^~]+)~~")
        for p in pages:
            m = _REV.search(p["text_markdown"])
            if m:
                result["revoked_sample"] = m.group(0)[:120]
                break
    else:
        result["pages_total"] = None

    result["chunks_count"] = len(chunks)
    if chunks:
        result["chunk_has_table"]   = sum(1 for c in chunks if c.get("has_table"))
        result["chunk_has_revoked"] = sum(1 for c in chunks if c.get("has_revoked"))

    result["vectors_in_qdrant"] = vecs
    return result


# ── report ──────────────────────────────────────────────────────────────────

def _status_icon(s: str | None) -> str:
    return {"ok": "OK", "error": "ERR", "pending": "...", "skipped": "skip"}.get(s or "", "?")


def print_report(results: list[dict]) -> None:
    sep = "-" * 90

    print()
    print("=" * 90)
    print("  ANEEL RAG -- TEST REPORT: 10 DOCUMENTS")
    print("=" * 90)

    for r in results:
        print()
        print(sep)
        print(f"  [{r['label']}]")
        print(f"  doc_id: {r['doc_id']}")
        print(f"  situacao_doc: {r['situacao_doc']}")
        print()

        # Pipeline stages
        stages = ["download", "parse", "chunk", "embed"]
        row_s = "  Pipeline: " + "  →  ".join(
            f"{s}[{_status_icon(r.get('status_'+s))}]" for s in stages
        )
        print(row_s)
        print(f"  parse_path_mix: {r.get('parse_path_mix')}")
        print()

        # Page breakdown
        if r.get("pages_total") is not None:
            print(f"  Pages: {r['pages_total']} total  |  "
                  f"digital={r['pages_digital']}  "
                  f"scanned={r['pages_scanned']}  "
                  f"complex={r['pages_complex']}  "
                  f"needs_vision={r['pages_vision']}")
            print(f"  Chars: {r['total_chars']:,}  |  "
                  f"Tables: {r['tables_count']}  |  "
                  f"has_table={r['has_table']}  |  "
                  f"has_revoked_text={r['has_revoked_text']}")
        else:
            print("  Parse output: NOT FOUND")

        # Chunks & vectors
        print(f"  Chunks: {r['chunks_count']}  |  "
              f"with_table={r.get('chunk_has_table',0)}  "
              f"with_revoked={r.get('chunk_has_revoked',0)}  |  "
              f"Qdrant vectors: {r['vectors_in_qdrant']}")

        # Samples
        if r.get("table_sample"):
            print(f"  Table sample: {r['table_sample'][:120]!r}")
        if r.get("revoked_sample"):
            print(f"  Revoked sample: {r['revoked_sample']!r}")

    print()
    print(sep)
    print()

    # Summary table
    print("SUMMARY")
    print(f"{'#':<3} {'Label':<35} {'Mix':<14} {'Pages':>5} {'Scan':>4} {'Tbl':>4} {'Rev':>4} {'Chk':>5} {'Vec':>5}")
    print("-" * 84)
    for i, r in enumerate(results, 1):
        print(
            f"{i:<3} {r['label'][:35]:<35} "
            f"{(r.get('parse_path_mix') or 'n/a'):<14} "
            f"{(r.get('pages_total') or 0):>5} "
            f"{(r.get('pages_scanned') or 0):>4} "
            f"{'Y' if r.get('has_table') else 'N':>4} "
            f"{'Y' if r.get('has_revoked_text') else 'N':>4} "
            f"{r['chunks_count']:>5} "
            f"{r['vectors_in_qdrant']:>5}"
        )
    print()


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    conn = _db()
    results = []
    for doc_id, label in TEST_DOCS:
        print(f"Analysing: {label}...", flush=True)
        results.append(analyse_doc(doc_id, label, conn))
    conn.close()
    print_report(results)

    # Issues
    issues = []
    for r in results:
        if r["status_download"] != "ok":
            issues.append(f"  ! {r['label']}: not downloaded")
        if r["status_parse"] == "ok" and r["pages_total"] is None:
            issues.append(f"  ! {r['label']}: parse ok but JSON missing")
        if r["status_chunk"] == "ok" and r["chunks_count"] == 0:
            issues.append(f"  ! {r['label']}: chunk ok but no JSONL")
    if issues:
        print("ISSUES DETECTED:")
        for i in issues:
            print(i)
    else:
        print("No issues detected.")
    print()


if __name__ == "__main__":
    main()
