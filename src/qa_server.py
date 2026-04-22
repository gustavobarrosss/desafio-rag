"""FastAPI wrapper around the QA pipeline.

Exposes the retriever + Gemini answer as an HTTP service. Intended to run on
the Compute Engine VM behind IAP (or any authenticated gateway).

Run locally:
    uvicorn src.qa_server:app --host 0.0.0.0 --port 8080
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .qa import ask

log = logging.getLogger(__name__)

app = FastAPI(title="ANEEL RAG QA", version="1.0.0")


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


@app.post("/ask", response_model=AskResponse)
async def ask_endpoint(req: AskRequest) -> AskResponse:
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
