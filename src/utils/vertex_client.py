"""Factory for Vertex AI genai clients using the dedicated LLM service account.

Keeps LLM credentials (project `bionic-medley-...`) isolated from infra ADC
(project `desafio-rag`). Loads explicit creds from VERTEX_LLM_CREDENTIALS_PATH
when set; otherwise falls back to ADC.
"""
from __future__ import annotations

import logging
from pathlib import Path

from google import genai

log = logging.getLogger(__name__)


def build_vertex_client(project: str, location: str, credentials_path: str = "") -> genai.Client:
    if credentials_path:
        path = Path(credentials_path)
        if not path.is_file():
            raise RuntimeError(f"VERTEX_LLM_CREDENTIALS_PATH not found: {credentials_path}")
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_file(
            str(path),
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        log.info("vertex client: project=%s location=%s (explicit SA)", project, location)
        return genai.Client(vertexai=True, project=project, location=location, credentials=creds)
    log.info("vertex client: project=%s location=%s (ADC)", project, location)
    return genai.Client(vertexai=True, project=project, location=location)
