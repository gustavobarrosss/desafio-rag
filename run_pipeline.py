"""CLI orchestrator for the ANEEL RAG ingestion pipeline."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )


def cmd_init(args: argparse.Namespace) -> None:
    from src.ingest_metadata import load_all
    n = load_all()
    print(json.dumps({"loaded_rows": n}))


def cmd_status(args: argparse.Namespace) -> None:
    from src.state import counts, init_db
    init_db()
    print(json.dumps(counts(), indent=2, ensure_ascii=False))


def cmd_download(args: argparse.Namespace) -> None:
    from src.download import run_downloads
    stats = asyncio.run(run_downloads(limit=args.limit))
    print(json.dumps(stats))


def cmd_parse(args: argparse.Namespace) -> None:
    from src.parse_runner import parse_batch
    stats = parse_batch(limit=args.limit)
    print(json.dumps(stats, ensure_ascii=False))


def cmd_vision(args: argparse.Namespace) -> None:
    from src.parse_vision_runner import run_vision_batch
    stats = asyncio.run(run_vision_batch(limit=args.limit))
    print(json.dumps(stats, ensure_ascii=False))


def cmd_chunk(args: argparse.Namespace) -> None:
    from src.chunker import chunk_batch
    stats = chunk_batch(limit=args.limit)
    print(json.dumps(stats, ensure_ascii=False))


def cmd_embed(args: argparse.Namespace) -> None:
    from src.ingest import ingest_pending
    stats = ingest_pending(limit=args.limit)
    print(json.dumps(stats, ensure_ascii=False))


def cmd_qa(args: argparse.Namespace) -> None:
    from src.qa import ask_sync
    ans = ask_sync(args.question, top_k=args.top_k)
    print(json.dumps({
        "answer": ans.answer,
        "model": ans.model,
        "citations": ans.citations,
    }, ensure_ascii=False, indent=2))


def cmd_evaluate(args: argparse.Namespace) -> None:
    from src.evaluate import evaluate
    result = evaluate(args.benchmark, top_k=args.top_k)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_all(args: argparse.Namespace) -> None:
    cmd_init(args); cmd_download(args); cmd_parse(args); cmd_vision(args); cmd_chunk(args); cmd_embed(args)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="run_pipeline")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="Load metadata JSONs into state DB").set_defaults(func=cmd_init)
    sub.add_parser("status", help="Show pipeline status counts").set_defaults(func=cmd_status)

    d = sub.add_parser("download", help="Download pending PDFs")
    d.add_argument("--limit", type=int, default=None)
    d.set_defaults(func=cmd_download)

    pa = sub.add_parser("parse", help="Fast-path parse downloaded PDFs")
    pa.add_argument("--limit", type=int, default=None)
    pa.set_defaults(func=cmd_parse)

    v = sub.add_parser("vision", help="Run vision path for flagged pages")
    v.add_argument("--limit", type=int, default=None)
    v.set_defaults(func=cmd_vision)

    c = sub.add_parser("chunk", help="Chunk parsed docs")
    c.add_argument("--limit", type=int, default=None)
    c.set_defaults(func=cmd_chunk)

    e = sub.add_parser("embed", help="Embed + upsert into Qdrant")
    e.add_argument("--limit", type=int, default=None)
    e.set_defaults(func=cmd_embed)

    q = sub.add_parser("qa", help="Ask a question")
    q.add_argument("question")
    q.add_argument("--top-k", type=int, default=6)
    q.set_defaults(func=cmd_qa)

    ev = sub.add_parser("evaluate", help="Run eval on a benchmark JSONL")
    ev.add_argument("benchmark")
    ev.add_argument("--top-k", type=int, default=6)
    ev.set_defaults(func=cmd_evaluate)

    a = sub.add_parser("all", help="init → download → parse → vision → chunk → embed")
    a.add_argument("--limit", type=int, default=None)
    a.set_defaults(func=cmd_all)

    return p


def main() -> None:
    args = build_parser().parse_args()
    _setup_logging(args.verbose)
    args.func(args)


if __name__ == "__main__":
    main()
