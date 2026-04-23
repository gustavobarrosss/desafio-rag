"""FastAPI wrapper around the QA pipeline.

Exposes the retriever + Gemini answer as an HTTP service. Intended to run on
the Compute Engine VM behind IAP (or any authenticated gateway).

Run locally:
    uvicorn src.qa_server:app --host 0.0.0.0 --port 8080
"""
from __future__ import annotations

import logging
import os
import secrets
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field

from .qa import ask

log = logging.getLogger(__name__)

_AUTH_USER = os.getenv("QA_USERNAME", "")
_AUTH_PASS = os.getenv("QA_PASSWORD", "")
_AUTH_ENABLED = bool(_AUTH_USER and _AUTH_PASS)
_security = HTTPBasic(auto_error=False)


def _require_auth(creds: HTTPBasicCredentials | None = Depends(_security)) -> str:
    if not _AUTH_ENABLED:
        return "anonymous"
    if creds is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credentials required",
            headers={"WWW-Authenticate": 'Basic realm="ANEEL RAG"'},
        )
    user_ok = secrets.compare_digest(creds.username.encode(), _AUTH_USER.encode())
    pass_ok = secrets.compare_digest(creds.password.encode(), _AUTH_PASS.encode())
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": 'Basic realm="ANEEL RAG"'},
        )
    return creds.username


app = FastAPI(
    title="ANEEL RAG QA",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    top_k: int | None = Field(default=None, ge=1, le=50)
    filters: dict[str, Any] | None = None


class Citation(BaseModel):
    doc_id: str
    article_ref: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    situacao_doc: str | None = None
    url: str | None = None
    score: float | None = None


class AskResponse(BaseModel):
    question: str
    answer: str
    model: str
    citations: list[Citation]


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/docs", include_in_schema=False)
def docs(_: str = Depends(_require_auth)):
    return get_swagger_ui_html(openapi_url="/openapi.json", title="ANEEL RAG — docs")


@app.get("/openapi.json", include_in_schema=False)
def openapi_json(_: str = Depends(_require_auth)):
    return get_openapi(title=app.title, version=app.version, routes=app.routes)


@app.post("/ask", response_model=AskResponse)
async def ask_endpoint(req: AskRequest, _: str = Depends(_require_auth)) -> AskResponse:
    try:
        result = await ask(req.question, filters=req.filters, top_k=req.top_k)
    except Exception as exc:
        log.exception("ask failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return AskResponse(
        question=result.question,
        answer=result.answer,
        model=result.model,
        citations=[Citation(**c) for c in result.citations],
    )
