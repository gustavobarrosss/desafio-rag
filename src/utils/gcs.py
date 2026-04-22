"""GCS helpers. Used only when GCS_BUCKET is configured (prod on VM)."""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)


def download_metadata_from_gcs(
    bucket: str,
    files: list[Path],
    prefix: str = "metadata",
) -> int:
    """Download missing metadata JSONs from `gs://{bucket}/{prefix}/{filename}`.

    Returns the number of files downloaded. Existing local files are not touched.
    Requires google-cloud-storage and ADC credentials.
    """
    missing = [p for p in files if not p.exists()]
    if not missing:
        return 0

    from google.cloud import storage  # type: ignore

    client = storage.Client()
    gcs_bucket = client.bucket(bucket)
    downloaded = 0
    for path in missing:
        path.parent.mkdir(parents=True, exist_ok=True)
        blob_name = f"{prefix.rstrip('/')}/{path.name}"
        blob = gcs_bucket.blob(blob_name)
        if not blob.exists():
            log.warning("metadata blob not found: gs://%s/%s", bucket, blob_name)
            continue
        blob.download_to_filename(str(path))
        downloaded += 1
        log.info("downloaded gs://%s/%s -> %s", bucket, blob_name, path)
    return downloaded
