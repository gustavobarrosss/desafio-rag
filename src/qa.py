"""Question answering on top of the hybrid retriever using Gemini via Vertex AI."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from google.genai.types import GenerateContentConfig, ThinkingConfig
from tenacity import AsyncRetrying, stop_after_attempt, wait_exponential_jitter

from .chunker import _clean_text
from .config import SETTINGS
from .retriever import RetrievedChunk, search
from .utils.rate_limiter import Budget, RateLimiter
from .utils.vertex_client import build_vertex_client

log = logging.getLogger(__name__)


SYSTEM_PROMPT = (
    "Você é um assistente jurídico especialista em regulação do setor elétrico brasileiro (ANEEL). "
    "Responda estritamente com base nos TRECHOS fornecidos. Siga estas regras:\n"
    "1) Cite as fontes usando [doc_id | art X] ou [doc_id | p. N] ao final de cada afirmação.\n"
    "2) Se um trecho contém texto entre ~~ ... ~~, esse texto está REVOGADO — nunca o trate como norma vigente; "
    "se precisar mencioná-lo, deixe explícito que foi revogado.\n"
    "3) Use todo conteúdo relevante dos trechos para compor a resposta, mesmo que a redação seja indireta ou o "
    "artigo não cite explicitamente o tema da pergunta. Diga 'não consta no contexto' SOMENTE quando nenhum "
    "trecho contiver informação relacionada ao tema — não use esse recurso por falta de citação direta.\n"
    "4) Desconsidere linhas de tabela malformadas (ex: | | | |) e foque no texto dos artigos e parágrafos.\n"
    "5) Responda em português do Brasil, de forma objetiva e técnica."
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
        parts.append(f"{header}\n{_clean_text(c.text)}")
    return "\n\n---\n\n".join(parts)


class QAClient:
    def __init__(self) -> None:
        cfg = SETTINGS.vision
        if not cfg.project:
            raise RuntimeError("GOOGLE_CLOUD_PROJECT not set")
        self.cfg = cfg
        self.limiter = RateLimiter(Budget(rpm=cfg.rpm, rpd=cfg.rpd))
        self._client = build_vertex_client(cfg.project, cfg.location, cfg.credentials_path)
        self._active_model = cfg.model

    async def close(self) -> None:
        pass

    async def _call(self, system_prompt: str, user_content: str, model: str, max_tokens: int = 1200) -> str:
        gen_config = GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.0,
            max_output_tokens=max_tokens,
            thinking_config=ThinkingConfig(thinking_budget=0),
        )
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: self._client.models.generate_content(
                model=model,
                contents=user_content,
                config=gen_config,
            ),
        )
        return response.text or ""

    async def generate(self, system_prompt: str, user_content: str, max_tokens: int = 300) -> str:
        await self.limiter.acquire()
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential_jitter(initial=2, max=30),
            reraise=True,
        ):
            with attempt:
                return await self._call(system_prompt, user_content, self._active_model, max_tokens)
        raise RuntimeError("unreachable")

    async def answer(self, question: str, chunks: list[RetrievedChunk]) -> Answer:
        await self.limiter.acquire()
        context = _format_chunks(chunks)
        user_content = f"TRECHOS:\n{context}\n\nPERGUNTA: {question}"
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(3),
                wait=wait_exponential_jitter(initial=2, max=30),
                reraise=True,
            ):
                with attempt:
                    text = await self._call(SYSTEM_PROMPT, user_content, self._active_model)
        except Exception:
            if self._active_model != self.cfg.fallback_model:
                log.warning("switching to fallback model %s", self.cfg.fallback_model)
                self._active_model = self.cfg.fallback_model
                text = await self._call(SYSTEM_PROMPT, user_content, self._active_model)
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
