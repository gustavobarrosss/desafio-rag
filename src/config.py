from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(ROOT / "data")))
PDF_DIR = Path(os.getenv("PDF_DIR", str(DATA_DIR / "pdfs")))
PARSED_DIR = Path(os.getenv("PARSED_DIR", str(DATA_DIR / "parsed")))
CHUNKS_DIR = Path(os.getenv("CHUNKS_DIR", str(DATA_DIR / "chunks")))
EVAL_DIR = Path(os.getenv("EVAL_DIR", str(DATA_DIR / "eval")))
STATE_DB = Path(os.getenv("STATE_DB_PATH", str(DATA_DIR / "state.sqlite")))

_METADATA_DIR = Path(os.getenv("METADATA_DIR", str(ROOT / "dados_grupo_estudos")))
METADATA_FILES = [
    _METADATA_DIR / "biblioteca_aneel_gov_br_legislacao_2016_metadados.json",
    _METADATA_DIR / "biblioteca_aneel_gov_br_legislacao_2021_metadados.json",
    _METADATA_DIR / "biblioteca_aneel_gov_br_legislacao_2022_metadados.json",
]

GCP_INFRA_PROJECT = os.getenv("GCP_INFRA_PROJECT", "")
GCP_LLM_PROJECT = os.getenv("GCP_LLM_PROJECT", os.getenv("GOOGLE_CLOUD_PROJECT", ""))
VERTEX_LLM_CREDENTIALS_PATH = os.getenv("VERTEX_LLM_CREDENTIALS_PATH", "")
GCS_BUCKET = os.getenv("GCS_BUCKET", "")
GCS_METADATA_PREFIX = os.getenv("GCS_METADATA_PREFIX", "metadata")


def bootstrap_metadata() -> int:
    """Ensure METADATA_FILES are present locally; fetch from GCS if needed.

    No-op when GCS_BUCKET is unset or all files already exist. Safe to call
    from CLI entrypoints (e.g. `run_pipeline.py init`).
    """
    if not GCS_BUCKET:
        return 0
    from .utils.gcs import download_metadata_from_gcs
    return download_metadata_from_gcs(GCS_BUCKET, METADATA_FILES, GCS_METADATA_PREFIX)


@dataclass
class DownloadConfig:
    concurrency: int = 16
    timeout_s: int = 90
    max_retries: int = 4
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )


@dataclass
class RouterConfig:
    min_chars_per_page: int = 60
    image_area_ratio_threshold: float = 0.55
    sample_pages_for_doc_classification: int = 3


@dataclass
class VisionConfig:
    model: str = field(default_factory=lambda: os.getenv("VERTEXAI_MODEL", "gemini-2.5-flash"))
    fallback_model: str = field(default_factory=lambda: os.getenv("VERTEXAI_FALLBACK_MODEL", "gemini-2.5-flash-lite"))
    project: str = field(default_factory=lambda: os.getenv("GCP_LLM_PROJECT", os.getenv("GOOGLE_CLOUD_PROJECT", "")))
    credentials_path: str = field(default_factory=lambda: os.getenv("VERTEX_LLM_CREDENTIALS_PATH", os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")))
    location: str = field(default_factory=lambda: os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"))
    rpm: int = field(default_factory=lambda: int(os.getenv("VERTEXAI_RPM", "60")))
    rpd: int = field(default_factory=lambda: int(os.getenv("VERTEXAI_RPD", "1000")))
    render_dpi: int = 180
    max_workers: int = 4


@dataclass
class ChunkerConfig:
    max_tokens: int = 900
    min_tokens: int = 60
    overlap_tokens: int = 100


@dataclass
class EmbedConfig:
    model: str = field(default_factory=lambda: os.getenv("EMBED_MODEL", "BAAI/bge-m3"))
    batch_size: int = field(default_factory=lambda: int(os.getenv("EMBED_BATCH", "16")))
    device: str = field(default_factory=lambda: os.getenv("EMBED_DEVICE", "cpu"))
    use_fp16: bool = True
    max_length: int = 1024


@dataclass
class QdrantConfig:
    url: str = field(default_factory=lambda: os.getenv("QDRANT_URL", "http://localhost:6333"))
    collection: str = field(default_factory=lambda: os.getenv("QDRANT_COLLECTION", "aneel_legis"))
    dense_dim: int = 1024
    upsert_batch: int = 128


@dataclass
class RetrieverConfig:
    top_k_dense: int = 40
    top_k_sparse: int = 40
    rrf_k: int = 60
    rerank_top_n: int = 20
    final_top_k: int = 6
    rerank_model: str = field(default_factory=lambda: os.getenv("RERANK_MODEL", "BAAI/bge-reranker-v2-m3"))


@dataclass
class Settings:
    download: DownloadConfig = field(default_factory=DownloadConfig)
    router: RouterConfig = field(default_factory=RouterConfig)
    vision: VisionConfig = field(default_factory=VisionConfig)
    chunker: ChunkerConfig = field(default_factory=ChunkerConfig)
    embed: EmbedConfig = field(default_factory=EmbedConfig)
    qdrant: QdrantConfig = field(default_factory=QdrantConfig)
    retriever: RetrieverConfig = field(default_factory=RetrieverConfig)


SETTINGS = Settings()


def ensure_dirs() -> None:
    for d in (DATA_DIR, PDF_DIR, PARSED_DIR, CHUNKS_DIR, EVAL_DIR):
        d.mkdir(parents=True, exist_ok=True)
