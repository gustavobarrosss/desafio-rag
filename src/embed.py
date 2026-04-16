"""BGE-M3 embedding wrapper (dense + sparse)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Sequence

import numpy as np

from .config import SETTINGS

log = logging.getLogger(__name__)


@dataclass
class EmbeddingBundle:
    dense: np.ndarray                 # (n, d)
    sparse: list[dict[int, float]]    # len n


@lru_cache(maxsize=1)
def _model():
    from FlagEmbedding import BGEM3FlagModel
    cfg = SETTINGS.embed
    log.info("loading embedding model %s on %s", cfg.model, cfg.device)
    return BGEM3FlagModel(cfg.model, use_fp16=cfg.use_fp16, device=cfg.device)


def embed_texts(texts: Sequence[str]) -> EmbeddingBundle:
    cfg = SETTINGS.embed
    model = _model()
    out = model.encode(
        list(texts),
        batch_size=cfg.batch_size,
        max_length=cfg.max_length,
        return_dense=True,
        return_sparse=True,
        return_colbert_vecs=False,
    )
    dense = np.asarray(out["dense_vecs"], dtype=np.float32)
    sparse_raw = out["lexical_weights"]
    sparse: list[dict[int, float]] = []
    for item in sparse_raw:
        sparse.append({int(k): float(v) for k, v in item.items()})
    return EmbeddingBundle(dense=dense, sparse=sparse)


def embed_query(text: str) -> tuple[np.ndarray, dict[int, float]]:
    bundle = embed_texts([text])
    return bundle.dense[0], bundle.sparse[0]
