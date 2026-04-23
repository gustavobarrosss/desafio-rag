"""Qdrant collection management + chunk upsert."""
from __future__ import annotations

import logging
import uuid
from typing import Iterable

from qdrant_client import QdrantClient, models
from qdrant_client.http.exceptions import UnexpectedResponse
from tqdm import tqdm

from .chunker import iter_all_chunks, load_chunks
from .config import SETTINGS
from .embed import embed_texts
from .state import mark_status, pending

log = logging.getLogger(__name__)

_PAYLOAD_INDEX_FIELDS = [
    ("ano", models.PayloadSchemaType.INTEGER),
    ("autor", models.PayloadSchemaType.KEYWORD),
    ("situacao_doc", models.PayloadSchemaType.KEYWORD),
    ("assunto", models.PayloadSchemaType.KEYWORD),
    ("has_revoked", models.PayloadSchemaType.BOOL),
    ("has_table", models.PayloadSchemaType.BOOL),
    ("tipo_pdf", models.PayloadSchemaType.KEYWORD),
    ("doc_id", models.PayloadSchemaType.KEYWORD),
]


def get_client() -> QdrantClient:
    return QdrantClient(url=SETTINGS.qdrant.url, prefer_grpc=False, timeout=60)


def ensure_collection(client: QdrantClient | None = None) -> None:
    client = client or get_client()
    cfg = SETTINGS.qdrant
    try:
        client.get_collection(cfg.collection)
        return
    except (UnexpectedResponse, ValueError):
        pass
    client.create_collection(
        collection_name=cfg.collection,
        vectors_config={
            "dense": models.VectorParams(size=cfg.dense_dim, distance=models.Distance.COSINE),
        },
        sparse_vectors_config={
            "sparse": models.SparseVectorParams(
                index=models.SparseIndexParams(on_disk=False),
            )
        },
    )
    for field, schema in _PAYLOAD_INDEX_FIELDS:
        try:
            client.create_payload_index(cfg.collection, field_name=field, field_schema=schema)
        except Exception as exc:
            log.debug("index %s already exists or failed: %s", field, exc)


_POINT_NS = uuid.UUID("f3e17e43-8f41-4f7b-9e48-7f5a6e4f8f1a")


def _build_point(chunk: dict, dense_vec, sparse_vec: dict[int, float]) -> models.PointStruct:
    point_id = str(uuid.uuid5(_POINT_NS, chunk["chunk_id"]))
    indices = list(sparse_vec.keys())
    values = [sparse_vec[i] for i in indices]
    return models.PointStruct(
        id=point_id,
        vector={
            "dense": dense_vec.tolist(),
            "sparse": models.SparseVector(indices=indices, values=values),
        },
        payload=chunk,
    )


def upsert_chunks(chunks: list[dict]) -> int:
    if not chunks:
        return 0
    client = get_client()
    ensure_collection(client)
    texts = [c["text"] for c in chunks]
    bundle = embed_texts(texts)
    points = [
        _build_point(c, bundle.dense[i], bundle.sparse[i])
        for i, c in enumerate(chunks)
    ]
    client.upsert(SETTINGS.qdrant.collection, points=points, wait=False)
    return len(points)


def ingest_pending(limit: int | None = None) -> dict:
    rows = pending("embed", limit=limit)
    ok = errors = 0
    total_points = 0
    batch_size = SETTINGS.qdrant.upsert_batch
    buffer: list[tuple[str, list[dict]]] = []

    def flush() -> None:
        nonlocal total_points, ok
        if not buffer:
            return
        flat = [c for _, cs in buffer for c in cs]
        n = upsert_chunks(flat)
        total_points += n
        for doc_id, _ in buffer:
            mark_status(doc_id, "embed", "ok")
            ok += 1
        buffer.clear()

    client = get_client()
    ensure_collection(client)

    def _delete_doc_vectors(doc_id: str) -> None:
        try:
            client.delete(
                SETTINGS.qdrant.collection,
                points_selector=models.FilterSelector(
                    filter=models.Filter(must=[
                        models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id))
                    ])
                ),
            )
        except Exception as exc:
            log.debug("delete old vectors for %s: %s", doc_id, exc)

    pending_count = 0
    for row in tqdm(rows, desc="embed"):
        doc_id = row["doc_id"]
        try:
            chunks = load_chunks(doc_id)
            _delete_doc_vectors(doc_id)
            if not chunks:
                mark_status(doc_id, "embed", "ok")
                ok += 1
                continue
            buffer.append((doc_id, chunks))
            pending_count += len(chunks)
            if pending_count >= batch_size:
                flush()
                pending_count = 0
        except Exception as exc:
            log.exception("ingest failed for %s", doc_id)
            mark_status(doc_id, "embed", "error", error=str(exc)[:240])
            errors += 1
    flush()
    return {"pending": len(rows), "ok": ok, "errors": errors, "points": total_points}
