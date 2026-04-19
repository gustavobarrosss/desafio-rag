"""Quick retrieval smoke test — hybrid search without the LLM layer.

Usage:
    python scripts/test_retrieval.py "Qual o prazo para revisão tarifária?"
    python scripts/test_retrieval.py "..." --top-k 10
    python scripts/test_retrieval.py "..." --ano 2022 --situacao-doc REVOGADA

No Vertex AI calls are made. Requires a populated Qdrant collection.
"""
from __future__ import annotations

import argparse
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.retriever import search  # noqa: E402


def _truncate(text: str, width: int = 300) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 1] + "…"


def main() -> int:
    p = argparse.ArgumentParser(description="Retriever smoke test (no LLM).")
    p.add_argument("query", help="Pergunta em linguagem natural")
    p.add_argument("--top-k", type=int, default=6)
    p.add_argument("--ano", type=int, help="Filtrar por ano (2016, 2021, 2022)")
    p.add_argument("--situacao-doc", help="Filtrar por situação (ex: REVOGADA)")
    p.add_argument("--snippet", type=int, default=280, help="Tamanho do trecho impresso")
    args = p.parse_args()

    filters: dict = {}
    if args.ano:
        filters["ano"] = args.ano
    if args.situacao_doc:
        filters["situacao_doc"] = args.situacao_doc

    chunks = search(args.query, filters=filters or None, top_k=args.top_k)

    if not chunks:
        print("Nenhum chunk retornado. Qdrant está populado?")
        return 1

    print(f"\nQuery: {args.query}")
    if filters:
        print(f"Filtros: {filters}")
    print(f"Top-{args.top_k} (reranker score):\n")

    for i, c in enumerate(chunks, 1):
        payload = c.payload
        header = (
            f"#{i}  score={c.score:.3f}  doc={c.doc_id}  "
            f"art={c.article_ref}  "
            f"pg={payload.get('page_start')}-{payload.get('page_end')}  "
            f"ano={payload.get('ano')}  sit={payload.get('situacao_doc')}"
        )
        print(header)
        print(textwrap.fill(_truncate(c.text, args.snippet), width=100, initial_indent="   ", subsequent_indent="   "))
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
