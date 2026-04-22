"""Vision-based page parser: Gemini via Vertex AI for scanned / complex pages."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import fitz
from google.genai.types import GenerateContentConfig, Part, ThinkingConfig
from tenacity import AsyncRetrying, stop_after_attempt, wait_exponential_jitter

from .config import PARSED_DIR, SETTINGS
from .parse_digital import load_parsed
from .utils.rate_limiter import Budget, RateLimiter
from .utils.vertex_client import build_vertex_client

log = logging.getLogger(__name__)


PROMPT = (
    "Você é um extrator de documentos legais brasileiros da ANEEL. "
    "Transcreva o conteúdo da página em markdown, preservando EXATAMENTE:\n"
    "1) A estrutura de artigos, parágrafos (§), incisos (I, II, III) e alíneas (a, b, c).\n"
    "2) Qualquer trecho TACHADO (strikethrough) deve aparecer como ~~texto tachado~~ — nunca omita.\n"
    "3) Tabelas como tabelas markdown (| col1 | col2 |), com cabeçalho e todas as linhas.\n"
    "4) Numeração original, fórmulas e referências.\n"
    "Não acrescente comentários nem resumos; devolva APENAS o conteúdo extraído."
)


@dataclass
class VisionResult:
    page_index: int
    markdown: str
    model_used: str
    status: str
    error: Optional[str] = None


def _page_png_b64(doc: fitz.Document, page_index: int, dpi: int) -> str:
    page = doc[page_index]
    pix = page.get_pixmap(dpi=dpi, alpha=False)
    return base64.b64encode(pix.tobytes("png")).decode("ascii")


class VisionClient:
    def __init__(self) -> None:
        cfg = SETTINGS.vision
        if not cfg.project:
            raise RuntimeError("GOOGLE_CLOUD_PROJECT not set")
        self.cfg = cfg
        self.limiter = RateLimiter(Budget(rpm=cfg.rpm, rpd=cfg.rpd))
        self._client = build_vertex_client(cfg.project, cfg.location, cfg.credentials_path)
        self._active_model = cfg.model
        self._fallback_used = False

    async def close(self) -> None:
        pass

    async def _call(self, img_b64: str, model: str) -> str:
        image_bytes = base64.b64decode(img_b64)
        image_part = Part.from_bytes(data=image_bytes, mime_type="image/png")
        gen_config = GenerateContentConfig(
            temperature=0.0,
            max_output_tokens=4096,
            thinking_config=ThinkingConfig(thinking_budget=0),
        )
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: self._client.models.generate_content(
                model=model,
                contents=[PROMPT, image_part],
                config=gen_config,
            ),
        )
        return response.text or ""

    async def transcribe(self, img_b64: str) -> tuple[str, str]:
        await self.limiter.acquire()
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(3),
                wait=wait_exponential_jitter(initial=2, max=30),
                reraise=True,
            ):
                with attempt:
                    text = await self._call(img_b64, self._active_model)
            return text, self._active_model
        except Exception as exc:
            if not self._fallback_used and self._active_model != self.cfg.fallback_model:
                log.warning("primary model failed (%s); switching to %s", exc, self.cfg.fallback_model)
                self._active_model = self.cfg.fallback_model
                self._fallback_used = True
                return await self.transcribe(img_b64)
            raise

    async def transcribe_pages(self, pdf_path: str, page_indexes: list[int]) -> list[VisionResult]:
        results: list[VisionResult] = []
        with fitz.open(pdf_path) as doc:
            for idx in page_indexes:
                try:
                    b64 = _page_png_b64(doc, idx, self.cfg.render_dpi)
                    text, model = await self.transcribe(b64)
                    results.append(VisionResult(page_index=idx, markdown=text, model_used=model, status="ok"))
                except Exception as exc:
                    log.warning("vision page %s failed: %s", idx, exc)
                    results.append(VisionResult(page_index=idx, markdown="", model_used=self._active_model,
                                                status="error", error=str(exc)[:240]))
        return results


def _vision_path(doc_id: str) -> Path:
    return PARSED_DIR / f"{doc_id}.vision.json"


def save_vision_results(doc_id: str, results: list[VisionResult]) -> Path:
    path = _vision_path(doc_id)
    path.write_text(
        json.dumps([r.__dict__ for r in results], ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def load_vision_results(doc_id: str) -> dict[int, VisionResult]:
    path = _vision_path(doc_id)
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {item["page_index"]: VisionResult(**item) for item in data}


async def run_vision_for_doc(client: VisionClient, doc_id: str, pdf_path: str) -> list[VisionResult]:
    parsed = load_parsed(doc_id)
    if not parsed:
        return []
    page_indexes = [p["page_index"] for p in parsed["pages"] if p.get("needs_vision")]
    if not page_indexes:
        return []
    existing = load_vision_results(doc_id)
    remaining = [i for i in page_indexes if i not in existing or existing[i].status != "ok"]
    if not remaining:
        return list(existing.values())
    new = await client.transcribe_pages(pdf_path, remaining)
    merged = {**existing}
    for r in new:
        merged[r.page_index] = r
    results = [merged[i] for i in sorted(merged)]
    save_vision_results(doc_id, results)
    return results


def merged_page_text(parsed_page: dict, vision: dict[int, VisionResult]) -> str:
    """Combine fast-path markdown with vision result when available."""
    idx = parsed_page["page_index"]
    v = vision.get(idx)
    if v is not None and v.status == "ok" and v.markdown.strip():
        return v.markdown.strip()
    text = (parsed_page.get("text_markdown") or "").strip()
    tables = parsed_page.get("tables_markdown") or []
    if tables:
        joined = "\n\n".join(tables)
        return f"{text}\n\n{joined}" if text else joined
    return text
