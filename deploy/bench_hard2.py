"""
Hard benchmark round 2: 12 NEW queries, same 6 categories.

Usage: python -u deploy/bench_hard2.py > bench_results2.txt
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
TIMEOUT = 180
RATE_SLEEP = 6.5  # Cohere Trial rate limit 10 RPM

BENCH = [
    # ── TABELA: valor numérico preciso ──────────────────────────────────
    {
        "category": "tabela",
        "label": "bandeira amarela 2016 valor",
        "question": "Qual o valor exato do adicional da bandeira tarifária amarela estabelecido em 2016? Responda em R$/MWh.",
    },
    {
        "category": "tabela",
        "label": "limite micro vs mini GD",
        "question": "Quais os limites de potência em kW que separam microgeração de minigeração distribuída na REN 482/2012 após alterações?",
    },
    # ── REVOGADAS: detalhamento de conteúdo revogado ────────────────────
    {
        "category": "revogada",
        "label": "art. 121 REN 414 (substituída)",
        "question": "O que dispunha o art. 121 da REN 414/2010? Essa norma ainda vale?",
    },
    {
        "category": "revogada",
        "label": "REN 482 alterada pela 687",
        "question": "Como a REN 687/2015 modificou a REN 482/2012 em relação ao Sistema de Compensação de Energia?",
    },
    # ── EMENTA: resposta só no sumário ──────────────────────────────────
    {
        "category": "ementa",
        "label": "Resolução Autorizativa 2718/2021",
        "question": "Em duas frases, qual o objeto da Resolução Autorizativa ANEEL n. 2718 de 2021?",
    },
    {
        "category": "ementa",
        "label": "Portaria 3935/2016",
        "question": "Sobre o que versa a Portaria ANEEL n. 3935 de 2016?",
    },
    # ── SCAN/OCR: docs potencialmente escaneados ─────────────────────────
    {
        "category": "scan_ocr",
        "label": "Nota Técnica 282/2021",
        "question": "O que analisa a Nota Técnica n. 282/2021-SGT/ANEEL?",
    },
    {
        "category": "scan_ocr",
        "label": "voto conduto GD 2022",
        "question": "Qual a análise técnica consolidada dos votos condutores da ANEEL em 2022 sobre adequação regulatória de geração distribuída?",
    },
    # ── CROSS-REFERÊNCIA ────────────────────────────────────────────────
    {
        "category": "cross_ref",
        "label": "Lei 13.360/2016 × REN 482",
        "question": "Como a Lei nº 13.360/2016 influenciou a REN 482/2012 em relação ao limite de potência hidráulica para minigeração distribuída?",
    },
    {
        "category": "cross_ref",
        "label": "REN 1000 × Módulo 3 PRODIST",
        "question": "Como a REN 1000/2021 se relaciona com o Módulo 3 dos Procedimentos de Distribuição (PRODIST)?",
    },
    # ── INTERPRETAÇÃO ───────────────────────────────────────────────────
    {
        "category": "interpret",
        "label": "recusa adesão SCEE",
        "question": "Em quais situações a distribuidora pode recusar a adesão de um consumidor ao Sistema de Compensação de Energia Elétrica (SCEE)?",
    },
    {
        "category": "interpret",
        "label": "bandeira vermelha × amarela",
        "question": "A aplicação da bandeira tarifária vermelha patamar 2 substitui a cobrança da bandeira amarela no mesmo mês? Explique.",
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
    print(f"Running {total} hard queries round 2 against {URL}\n")
    total_t0 = time.perf_counter()

    for i, item in enumerate(BENCH, 1):
        cat = item["category"]
        label = item["label"]
        question = item["question"]
        if i > 1:
            time.sleep(RATE_SLEEP)
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
        al = answer.lower()
        refusal_prefix = al.startswith("não consta") or al.startswith("nao consta")
        substantive = len(answer) > 300
        rejected = refusal_prefix and not substantive
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
