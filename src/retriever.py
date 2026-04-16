"""Hybrid retriever: dense + sparse from Qdrant, fused via RRF, reranked with BGE-m3."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from qdrant_client import QdrantClient, models

from .config import SETTINGS
from .embed import embed_query
from .ingest import get_client

log = logging.getLogger(__name__)


@dataclass
class RetrievedChunk:
    chunk_id: str
    doc_id: str
    text: str
    score: float
    payload: dict

    @property
    def article_ref(self) -> str | None:
        return self.payload.get("article_ref")


@lru_cache(maxsize=1)
def _reranker():
    from FlagEmbedding import FlagReranker
    cfg = SETTINGS.retriever
    log.info("loading reranker %s", cfg.rerank_model)
    return FlagReranker(cfg.rerank_model, use_fp16=SETTINGS.embed.use_fp16, device=SETTINGS.embed.device)


def _build_filter(filters: dict[str, Any] | None) -> models.Filter | None:
    if not filters:
        return None
    must: list[models.FieldCondition] = []
    for key, value in filters.items():
        if isinstance(value, bool):
            must.append(models.FieldCondition(key=key, match=models.MatchValue(value=value)))
        elif isinstance(value, (int, float, str)):
            must.append(models.FieldCondition(key=key, match=models.MatchValue(value=value)))
        elif isinstance(value, (list, tuple, set)):
            must.append(models.FieldCondition(key=key, match=models.MatchAny(any=list(value))))
    return models.Filter(must=must) if must else None


def _rrf_fuse(ranks: list[list[str]], k: int) -> dict[str, float]:
    scores: dict[str, float] = {}
    for ranking in ranks:
        for i, chunk_id in enumerate(ranking):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + i + 1)
    return scores


def search(query: str, *, filters: dict[str, Any] | None = None, top_k: int | None = None) -> list[RetrievedChunk]:
    cfg = SETTINGS.retriever
    top_k = top_k or cfg.final_top_k
    client: QdrantClient = get_client()
    qf = _build_filter(filters)

    dense_vec, sparse_vec = embed_query(query)
    sparse_q = models.SparseVector(
        indices=list(sparse_vec.keys()),
        values=[sparse_vec[i] for i in sparse_vec],
    )

    dense_hits = client.query_points(
        SETTINGS.qdrant.collection,
        query=dense_vec.tolist(),
        using="dense",
        limit=cfg.top_k_dense,
        query_filter=qf,
        with_payload=True,
    ).points

    sparse_hits = client.query_points(
        SETTINGS.qdrant.collection,
        query=sparse_q,
        using="sparse",
        limit=cfg.top_k_sparse,
        query_filter=qf,
        with_payload=True,
    ).points

    payloads: dict[str, dict] = {}
    for hit in dense_hits + sparse_hits:
        cid = hit.payload.get("chunk_id") or str(hit.id)
        payloads[cid] = hit.payload

    ranks = [
        [hit.payload.get("chunk_id") or str(hit.id) for hit in dense_hits],
        [hit.payload.get("chunk_id") or str(hit.id) for hit in sparse_hits],
    ]
    fused = _rrf_fuse(ranks, k=cfg.rrf_k)
    top_fused = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[:cfg.rerank_top_n]

    candidates = [payloads[cid] for cid, _ in top_fused if cid in payloads]
    if not candidates:
        return []

    pairs = [(query, c["text"]) for c in candidates]
    scores = _reranker().compute_score(pairs, normalize=True)
    if isinstance(scores, float):
        scores = [scores]
    order = sorted(range(len(candidates)), key=lambda i: scores[i], reverse=True)[:top_k]

    return [
        RetrievedChunk(
            chunk_id=candidates[i].get("chunk_id", ""),
            doc_id=candidates[i].get("doc_id", ""),
            text=candidates[i].get("text", ""),
            score=float(scores[i]),
            payload=candidates[i],
        )
        for i in order
    ]
