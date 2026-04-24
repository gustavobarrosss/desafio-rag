"""Hybrid retriever: dense + sparse + identifier lookup, fused via RRF, reranked.

Reranker:
- If COHERE_API_KEY is set, uses Cohere Rerank API (multilingual v3) — fast, no GPU.
- Otherwise falls back to BGE-reranker-v2-m3 on local CPU/GPU (slow on CPU)."""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from qdrant_client import QdrantClient, models

from .config import SETTINGS
from .embed import embed_query
from .ingest import get_client

log = logging.getLogger(__name__)

_EMENTA_PENALTY = 0.85  # demotion factor for page_start == 0 (ementa) chunks
_IDENT_NUM_RE = re.compile(r"\b(\d{3,5})\b")
_YEAR_RE = re.compile(r"\b(201[0-9]|202[0-5])\b")
_MAX_IDENT_DOCS = 40

_COHERE_KEY = os.getenv("COHERE_API_KEY", "").strip()
_COHERE_MODEL = os.getenv("COHERE_RERANK_MODEL", "rerank-multilingual-v3.0")


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
def _bge_reranker():
    from FlagEmbedding import FlagReranker
    cfg = SETTINGS.retriever
    log.info("loading BGE reranker %s", cfg.rerank_model)
    return FlagReranker(cfg.rerank_model, use_fp16=SETTINGS.embed.use_fp16, device=SETTINGS.embed.device)


@lru_cache(maxsize=1)
def _cohere_client():
    import cohere
    log.info("using Cohere rerank model=%s", _COHERE_MODEL)
    return cohere.Client(_COHERE_KEY)


def _cohere_rerank_with_retry(query: str, texts: list[str]) -> list[float]:
    """Call Cohere rerank with retry/backoff on rate-limit (429) and transient errors."""
    from tenacity import (
        retry,
        retry_if_exception_type,
        stop_after_attempt,
        wait_exponential_jitter,
    )

    try:
        import cohere.errors as _cohere_errors
        _retry_exc = (_cohere_errors.TooManyRequestsError, _cohere_errors.InternalServerError)
    except Exception:
        _retry_exc = (Exception,)

    @retry(
        retry=retry_if_exception_type(_retry_exc),
        stop=stop_after_attempt(5),
        wait=wait_exponential_jitter(initial=2, max=30),
        reraise=True,
    )
    def _call() -> list[float]:
        co = _cohere_client()
        res = co.rerank(model=_COHERE_MODEL, query=query, documents=texts, top_n=len(texts))
        scores = [0.0] * len(texts)
        for r in res.results:
            scores[r.index] = float(r.relevance_score)
        return scores

    return _call()


def _rerank_scores(query: str, texts: list[str]) -> list[float]:
    """Return one relevance score per input text, normalized to [0, 1]."""
    if not texts:
        return []
    if _COHERE_KEY:
        return _cohere_rerank_with_retry(query, texts)
    pairs = [(query, t) for t in texts]
    raw = _bge_reranker().compute_score(pairs, normalize=True)
    if isinstance(raw, float):
        raw = [raw]
    return [float(s) for s in raw]


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


@lru_cache(maxsize=1)
def _doc_index() -> list[tuple[str, str]]:
    """Unique (doc_id, arquivo_lower) built once via Qdrant scroll."""
    client = get_client()
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    next_page: Any = None
    while True:
        points, next_page = client.scroll(
            SETTINGS.qdrant.collection,
            limit=10000,
            offset=next_page,
            with_payload=["doc_id", "arquivo"],
            with_vectors=False,
        )
        for p in points:
            did = p.payload.get("doc_id")
            arq = (p.payload.get("arquivo") or "").lower()
            if did and did not in seen:
                seen.add(did)
                out.append((did, arq))
        if next_page is None:
            break
    log.info("doc_index built: %d unique docs", len(out))
    return out


def _identifier_doc_ids(query: str) -> list[str]:
    """Return doc_ids whose arquivo matches number(s) in query, scoped by year when present."""
    q = query.lower()
    numbers = [t for t in _IDENT_NUM_RE.findall(q)]
    years = [t for t in _YEAR_RE.findall(q)]
    # strip year-like tokens from "numbers" to avoid matching by year alone
    nums = [n for n in numbers if n not in years]
    if not nums:
        return []
    matched: list[str] = []
    for did, arq in _doc_index():
        if years:
            # require at least one year AND one number both present in arquivo
            if any(y in arq for y in years) and any(n in arq for n in nums):
                matched.append(did)
        else:
            if any(n in arq for n in nums):
                matched.append(did)
        if len(matched) >= _MAX_IDENT_DOCS:
            break
    return matched


def _rrf_fuse(ranks: list[list[str]], k: int) -> dict[str, float]:
    scores: dict[str, float] = {}
    for ranking in ranks:
        for i, chunk_id in enumerate(ranking):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + i + 1)
    return scores


def search(query: str, *, filters: dict[str, Any] | None = None, top_k: int | None = None) -> list[RetrievedChunk]:
    import time
    cfg = SETTINGS.retriever
    top_k = top_k or cfg.final_top_k
    client: QdrantClient = get_client()
    qf = _build_filter(filters)

    t0 = time.perf_counter()
    dense_vec, sparse_vec = embed_query(query)
    t1 = time.perf_counter()
    log.warning("[timing] embed_query=%.2fs", t1 - t0)
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

    ident_hits: list = []
    ident_doc_ids = _identifier_doc_ids(query)
    if ident_doc_ids:
        ident_filter_must = [
            models.FieldCondition(key="doc_id", match=models.MatchAny(any=ident_doc_ids))
        ]
        if qf and qf.must:
            ident_filter_must = list(qf.must) + ident_filter_must
        ident_hits = client.query_points(
            SETTINGS.qdrant.collection,
            query=dense_vec.tolist(),
            using="dense",
            limit=cfg.top_k_dense,
            query_filter=models.Filter(must=ident_filter_must),
            with_payload=True,
        ).points
        log.info("identifier lookup matched %d docs, %d chunks", len(ident_doc_ids), len(ident_hits))

    payloads: dict[str, dict] = {}
    for hit in dense_hits + sparse_hits + ident_hits:
        cid = hit.payload.get("chunk_id") or str(hit.id)
        payloads[cid] = hit.payload

    ranks = [
        [hit.payload.get("chunk_id") or str(hit.id) for hit in dense_hits],
        [hit.payload.get("chunk_id") or str(hit.id) for hit in sparse_hits],
    ]
    if ident_hits:
        ranks.append([hit.payload.get("chunk_id") or str(hit.id) for hit in ident_hits])
    fused = _rrf_fuse(ranks, k=cfg.rrf_k)
    top_fused = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[:cfg.rerank_top_n]

    # Hard-filter path: if identifier lookup matched ≤ 3 docs, restrict search to them.
    # User asking about a specific document by number — broad context is noise.
    if ident_doc_ids and len(ident_doc_ids) <= 3 and ident_hits:
        candidates = [hit.payload for hit in ident_hits]
    else:
        candidates = [payloads[cid] for cid, _ in top_fused if cid in payloads]
        if ident_hits:
            ident_cids_in_candidates = {c.get("chunk_id") for c in candidates}
            for hit in ident_hits[:cfg.rerank_top_n // 2]:
                cid = hit.payload.get("chunk_id") or str(hit.id)
                if cid not in ident_cids_in_candidates:
                    candidates.append(hit.payload)
                    ident_cids_in_candidates.add(cid)

    if not candidates:
        return []

    ident_cid_set = {
        (h.payload.get("chunk_id") or str(h.id)) for h in ident_hits
    } if ident_hits else set()

    t2 = time.perf_counter()
    log.warning("[timing] qdrant+ident=%.2fs (candidates=%d)", t2 - t1, len(candidates))

    rerank_texts = [
        f"[arquivo: {c.get('arquivo','')}]\n{c.get('text','')}"
        for c in candidates
    ]
    scores = _rerank_scores(query, rerank_texts)
    t3 = time.perf_counter()
    log.warning("[timing] rerank=%.2fs (pairs=%d)", t3 - t2, len(rerank_texts))
    scores = [
        s
        * (_EMENTA_PENALTY if candidates[i].get("page_start", 1) == 0 else 1.0)
        * (1.5 if candidates[i].get("chunk_id") in ident_cid_set else 1.0)
        for i, s in enumerate(scores)
    ]
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
