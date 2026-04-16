"""Evaluation harness: retriever hit@k + LLM-as-judge on answers."""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .config import EVAL_DIR, ensure_dirs
from .qa import QAClient
from .retriever import search

log = logging.getLogger(__name__)


@dataclass
class EvalCase:
    question: str
    expected_doc_ids: list[str] | None = None
    gold_answer: str | None = None
    filters: dict | None = None
    id: str | None = None


@dataclass
class CaseResult:
    case: EvalCase
    retrieved_doc_ids: list[str]
    hit_at_k: dict[int, bool]
    answer: str
    citations: list[dict]
    judge_score: float | None = None
    judge_rationale: str | None = None


def _load_cases(path: Path) -> list[EvalCase]:
    cases: list[EvalCase] = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        data = json.loads(line)
        cases.append(EvalCase(
            id=data.get("id", f"case_{i:04d}"),
            question=data["question"],
            expected_doc_ids=data.get("expected_doc_ids"),
            gold_answer=data.get("gold_answer"),
            filters=data.get("filters"),
        ))
    return cases


def _hit_at_k(expected: list[str], retrieved: list[str]) -> dict[int, bool]:
    out: dict[int, bool] = {}
    for k in (1, 3, 5, 10):
        out[k] = any(d in retrieved[:k] for d in (expected or []))
    return out


JUDGE_PROMPT = (
    "Você é um avaliador. Dada uma pergunta, uma resposta-gold e uma resposta-candidata, "
    "classifique a resposta-candidata em uma nota de 0 a 5 conforme:\n"
    "5 = cobre todo o conteúdo essencial do gold, sem erros;\n"
    "3 = parcialmente correta;\n"
    "0 = incorreta/alucinada.\n"
    "Retorne JSON {\"score\": <int>, \"rationale\": \"...\"}."
)


async def _judge(client: QAClient, question: str, gold: str, candidate: str) -> tuple[float | None, str | None]:
    await client.limiter.acquire()
    payload = {
        "model": client._active_model,
        "messages": [
            {"role": "system", "content": JUDGE_PROMPT},
            {"role": "user", "content": f"PERGUNTA: {question}\n\nGOLD:\n{gold}\n\nCANDIDATA:\n{candidate}"},
        ],
        "temperature": 0.0,
        "max_tokens": 300,
    }
    try:
        resp = await client._client.post("/chat/completions", json=payload)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        start = content.find("{")
        end = content.rfind("}")
        if start < 0 or end < 0:
            return None, content[:240]
        parsed = json.loads(content[start:end + 1])
        return float(parsed.get("score", 0)), str(parsed.get("rationale", ""))[:500]
    except Exception as exc:
        log.warning("judge failed: %s", exc)
        return None, None


async def run_eval(cases: Iterable[EvalCase], top_k: int = 6) -> list[CaseResult]:
    ensure_dirs()
    client = QAClient()
    results: list[CaseResult] = []
    try:
        for case in cases:
            chunks = search(case.question, filters=case.filters, top_k=top_k)
            retrieved_docs = [c.doc_id for c in chunks]
            answer = await client.answer(case.question, chunks)
            judge_score = judge_rationale = None
            if case.gold_answer:
                judge_score, judge_rationale = await _judge(client, case.question, case.gold_answer, answer.answer)
            results.append(CaseResult(
                case=case,
                retrieved_doc_ids=retrieved_docs,
                hit_at_k=_hit_at_k(case.expected_doc_ids or [], retrieved_docs),
                answer=answer.answer,
                citations=answer.citations,
                judge_score=judge_score,
                judge_rationale=judge_rationale,
            ))
    finally:
        await client.close()
    return results


def _summarize(results: list[CaseResult]) -> dict:
    n = len(results)
    if not n:
        return {}
    agg: dict[str, float] = {}
    for k in (1, 3, 5, 10):
        agg[f"hit@{k}"] = sum(1 for r in results if r.hit_at_k.get(k)) / n
    scored = [r.judge_score for r in results if r.judge_score is not None]
    if scored:
        agg["judge_mean"] = sum(scored) / len(scored)
        agg["judge_n"] = len(scored)
    return agg


def evaluate(benchmark_path: str, top_k: int = 6) -> dict:
    cases = _load_cases(Path(benchmark_path))
    results = asyncio.run(run_eval(cases, top_k=top_k))
    summary = _summarize(results)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = EVAL_DIR / f"eval_{ts}.jsonl"
    with out_path.open("w", encoding="utf-8") as fp:
        for r in results:
            fp.write(json.dumps({
                "id": r.case.id,
                "question": r.case.question,
                "retrieved_doc_ids": r.retrieved_doc_ids,
                "hit_at_k": r.hit_at_k,
                "answer": r.answer,
                "citations": r.citations,
                "judge_score": r.judge_score,
                "judge_rationale": r.judge_rationale,
            }, ensure_ascii=False) + "\n")
    log.info("wrote %s", out_path)
    return {"summary": summary, "report": str(out_path)}
