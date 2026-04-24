"""
Hard benchmark: 12 queries across 6 difficulty categories.

Usage: python deploy/bench_hard.py
"""
from __future__ import annotations

import base64
import json
import sys
import time
import urllib.error
import urllib.request

URL = "http://34.71.134.25:8080/ask"
USER = "desafio-rag"
PASS = "queria_uma_bolsa_rs"
TIMEOUT = 180  # per-query timeout seconds

BENCH = [
    # ── TABELA: valores numéricos precisos ────────────────────────────────
    {
        "category": "tabela",
        "label": "bandeira vermelha P2 2016 — valor exato",
        "question": "Qual foi o valor exato do adicional da bandeira tarifária vermelha patamar 2 estabelecido para 2016? Responda o valor em R$/MWh.",
    },
    {
        "category": "tabela",
        "label": "prazo máximo débitos religação",
        "question": "Segundo a ANEEL, qual é o prazo máximo em meses durante o qual um débito pretérito pode ser cobrado para condicionar a religação de energia?",
    },
    # ── REVOGADAS: detecção e descrição do conteúdo revogado ──────────────
    {
        "category": "revogada",
        "label": "Art. 4º-A REN 482/2012 (revogado)",
        "question": "O que dispunha o art. 4º-A da REN 482/2012 antes de ser revogado? A norma ainda está em vigor?",
    },
    {
        "category": "revogada",
        "label": "REN 414/2010 substituída",
        "question": "Quais resoluções normativas da ANEEL foram expressamente revogadas pela REN 1000/2021?",
    },
    # ── EMENTA: pergunta responsável apenas pela ementa/sumário ───────────
    {
        "category": "ementa",
        "label": "objeto Despacho 1442/2021",
        "question": "Em no máximo duas frases, qual é o objeto do Despacho ANEEL n. 1442 de 2021?",
    },
    {
        "category": "ementa",
        "label": "sumário REN 928/2021",
        "question": "Sobre o que versa a Resolução Normativa ANEEL n. 928 de 2021? Responda em uma frase.",
    },
    # ── PDFs com OCR / estrutura complexa ─────────────────────────────────
    {
        "category": "scan_ocr",
        "label": "Nota Técnica 222/2021-SGT recomendação",
        "question": "O que recomenda a Nota Técnica nº 222/2021-SGT/ANEEL?",
    },
    {
        "category": "scan_ocr",
        "label": "voto sobre inadimplência",
        "question": "Em processos envolvendo inadimplência e religação de consumidor, qual é o entendimento consolidado da Superintendência da ANEEL sobre a exigência de débitos pretéritos?",
    },
    # ── CROSS-REFERÊNCIA entre documentos ─────────────────────────────────
    {
        "category": "cross_ref",
        "label": "art. 128 REN 414 vs art. 356 REN 1000",
        "question": "Como o art. 128 da REN 414/2010 se relaciona com o art. 356 da REN 1000/2021 em termos de suspensão de fornecimento por inadimplência?",
    },
    {
        "category": "cross_ref",
        "label": "Lei 14.300/2022 e REN 482/2012",
        "question": "Como a Lei nº 14.300/2022 impactou a REN 482/2012 sobre geração distribuída?",
    },
    # ── INTERPRETAÇÃO jurídica complexa ───────────────────────────────────
    {
        "category": "interpret",
        "label": "débito pretérito não-contemporâneo",
        "question": "Pode a distribuidora suspender o fornecimento de energia em razão de débito pretérito não contemporâneo à interrupção? Justifique com base na regulação da ANEEL.",
    },
    {
        "category": "interpret",
        "label": "compensação créditos entre distribuidoras",
        "question": "A ANEEL permite a compensação de créditos de geração distribuída entre distribuidoras diferentes? Por que?",
    },
]


def _auth_header() -> str:
    token = base64.b64encode(f"{USER}:{PASS}".encode()).decode()
    return f"Basic {token}"


def ask(question: str) -> tuple[float, dict]:
    body = json.dumps({"question": question, "top_k": 8}).encode()
    req = urllib.request.Request(
        URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": _auth_header(),
        },
        method="POST",
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        data = json.loads(resp.read())
    elapsed = time.perf_counter() - t0
    return elapsed, data


def main() -> int:
    total = len(BENCH)
    by_cat: dict[str, list[tuple[str, float, bool]]] = {}
    print(f"Running {total} hard queries against {URL}\n")
    total_t0 = time.perf_counter()

    for i, item in enumerate(BENCH, 1):
        cat = item["category"]
        label = item["label"]
        question = item["question"]
        print(f"[{i:2d}/{total}] [{cat}] {label}")
        print(f"   Q: {question}")
        try:
            elapsed, d = ask(question)
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"   ERROR: {exc}\n")
            by_cat.setdefault(cat, []).append((label, -1.0, False))
            continue

        answer = (d.get("answer") or "").strip()
        cits = d.get("citations") or []
        # Heuristic: pass if answer doesn't begin with "não consta" and has ≥1 citation.
        rejected = answer.lower().startswith("não consta") or answer.lower().startswith("nao consta")
        passed = bool(cits) and not rejected
        by_cat.setdefault(cat, []).append((label, elapsed, passed))

        status = "PASS" if passed else ("REJECTED" if rejected else "NO-CIT")
        print(f"   A ({elapsed:.2f}s, {status}): {answer[:260]}{'...' if len(answer) > 260 else ''}")
        top = cits[0] if cits else None
        if top:
            print(
                f"   top citation: {top.get('doc_id')} "
                f"score={top.get('score', 0):.3f} "
                f"situacao={top.get('situacao_doc')} "
                f"art={(top.get('article_ref') or '')[:40]}"
            )
        print()

    total_elapsed = time.perf_counter() - total_t0
    print("=" * 72)
    print("SUMMARY")
    print("=" * 72)
    for cat, items in by_cat.items():
        n_pass = sum(1 for _, _, p in items if p)
        avg_t = sum(t for _, t, _ in items if t > 0) / max(sum(1 for _, t, _ in items if t > 0), 1)
        print(f"  {cat:12s} {n_pass}/{len(items)} pass   avg {avg_t:5.2f}s")
        for lbl, t, p in items:
            mark = "PASS" if p else "FAIL"
            print(f"    {mark} {lbl}  ({t:.2f}s)" if t > 0 else f"    {mark} {lbl}  (FAILED)")
    print(f"\nTotal wall time: {total_elapsed:.1f}s ({total_elapsed/total:.1f}s avg per query)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
