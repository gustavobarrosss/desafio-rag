"""Question answering on top of the hybrid retriever using Gemma via OpenRouter."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import httpx
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from .config import SETTINGS
from .retriever import RetrievedChunk, search
from .utils.rate_limiter import Budget, RateLimiter

log = logging.getLogger(__name__)


SYSTEM_PROMPT = (
    "Você é um assistente jurídico especialista em regulação do setor elétrico brasileiro (ANEEL). "
    "Responda estritamente com base nos TRECHOS fornecidos. Siga estas regras:\n"
    "1) Cite as fontes usando [doc_id | art X] ou [doc_id | p. N] ao final de cada afirmação.\n"
    "2) Se um trecho contém texto entre ~~ ... ~~, esse texto está REVOGADO — nunca o trate como norma vigente, "
    "e se precisar mencioná-lo deixe explícito que foi revogado.\n"
    "3) Se os trechos não trazem resposta suficiente, diga claramente que a informação não consta no contexto.\n"
    "4) Responda em português do Brasil, de forma objetiva e técnica."
)


@dataclass
class Answer:
    question: str
    answer: str
    citations: list[dict[str, Any]]
    model: str


def _format_chunks(chunks: list[RetrievedChunk]) -> str:
    parts: list[str] = []
    for i, c in enumerate(chunks, 1):
        payload = c.payload
        header = (
            f"[{i}] doc_id={c.doc_id} | titulo={payload.get('titulo')} | "
            f"ano={payload.get('ano')} | autor={payload.get('autor')} | "
            f"situacao_doc={payload.get('situacao_doc')} | art={c.article_ref} | "
            f"has_revoked={payload.get('has_revoked')} | pg {payload.get('page_start')}-{payload.get('page_end')}"
        )
        parts.append(f"{header}\n{c.text}")
    return "\n\n---\n\n".join(parts)


class QAClient:
    def __init__(self) -> None:
        cfg = SETTINGS.vision
        if not cfg.api_key:
            raise RuntimeError("OPENROUTER_API_KEY not set")
        self.cfg = cfg
        self.limiter = RateLimiter(Budget(rpm=cfg.rpm, rpd=cfg.rpd))
        self._client = httpx.AsyncClient(
            base_url=cfg.base_url,
            timeout=httpx.Timeout(120),
            headers={
                "Authorization": f"Bearer {cfg.api_key}",
                "HTTP-Referer": "https://github.com/ceia-desafio-rag",
                "X-Title": "aneel-rag-qa",
            },
        )
        self._active_model = cfg.model

    async def close(self) -> None:
        await self._client.aclose()

    async def _call(self, messages: list[dict[str, Any]], model: str) -> str:
        payload = {"model": model, "messages": messages, "temperature": 0.0, "max_tokens": 1200}
        resp = await self._client.post("/chat/completions", json=payload)
        if resp.status_code == 404:
            raise ValueError(f"model {model} not found on OpenRouter")
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    async def answer(self, question: str, chunks: list[RetrievedChunk]) -> Answer:
        await self.limiter.acquire()
        context = _format_chunks(chunks)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"TRECHOS:\n{context}\n\nPERGUNTA: {question}"},
        ]
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(3),
                wait=wait_exponential_jitter(initial=2, max=30),
                retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TransportError)),
                reraise=True,
            ):
                with attempt:
                    text = await self._call(messages, self._active_model)
        except ValueError:
            if self._active_model != self.cfg.fallback_model:
                log.warning("switching to fallback model %s", self.cfg.fallback_model)
                self._active_model = self.cfg.fallback_model
                text = await self._call(messages, self._active_model)
            else:
                raise
        citations = [
            {
                "doc_id": c.doc_id,
                "article_ref": c.article_ref,
                "page_start": c.payload.get("page_start"),
                "page_end": c.payload.get("page_end"),
                "situacao_doc": c.payload.get("situacao_doc"),
                "url": c.payload.get("url"),
                "score": c.score,
            }
            for c in chunks
        ]
        return Answer(question=question, answer=text, citations=citations, model=self._active_model)


async def ask(question: str, *, filters: dict[str, Any] | None = None, top_k: int | None = None) -> Answer:
    chunks = search(question, filters=filters, top_k=top_k)
    client = QAClient()
    try:
        return await client.answer(question, chunks)
    finally:
        await client.close()


def ask_sync(question: str, **kwargs) -> Answer:
    return asyncio.run(ask(question, **kwargs))
