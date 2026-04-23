# ANEEL RAG — Guia de Uso

Guia rápido para subir o servidor na GCP, parar quando não estiver usando, e consultar o endpoint `/ask` com exemplos prontos.

---

## 1. Subir / parar o servidor

A VM `aneel-rag-vm` (Compute Engine, `e2-standard-4`, us-central1-a) hospeda dois containers:

- `aneel-qdrant` — índice vetorial (124.822 chunks, BGE-M3 dense + sparse)
- `aneel-qa` — FastAPI com `/ask`, `/health`, `/docs`

Cobrança de compute acontece apenas enquanto a VM está `RUNNING`. Pause entre sessões.

### Subir

```bash
bash deploy/start.sh
```

O que faz:

1. Verifica estado atual da VM (evita ação redundante).
2. Liga a VM se estiver parada e aguarda `RUNNING`.
3. Aguarda SSH ficar acessível.
4. Aguarda containers `aneel-qa` e `aneel-qdrant` ficarem `healthy`.
5. Probe interno `/health`.
6. **Aquecimento**: dispara uma `/ask` boba para forçar o carregamento de BGE-M3 + reranker + `doc_index` (senão a primeira chamada real leva 1–3 min).

Ao final imprime IP externo e o próximo comando.

### Parar

```bash
bash deploy/stop.sh
```

1. Verifica estado (se já está `TERMINATED`, sai sem fazer nada).
2. Emite `stop` e aguarda a VM parar.
3. Custos de compute param. Disco SSD persiste (~US$ 8/mês).

---

## 2. Acesso à API

| Rota | Método | Auth | Finalidade |
|---|---|---|---|
| `/health` | GET | aberta | liveness probe |
| `/docs` | GET | Basic Auth | Swagger UI (formulário interativo) |
| `/ask` | POST | Basic Auth | consulta RAG |

### URL pública

```
http://34.132.112.29:8080
```

> **⚠️ HTTP sem TLS.** Senha trafega em texto claro. OK para benchmark/demo de curto prazo — rotacione depois.

### Credenciais

- **user:** `desafio-rag`
- **senha:** `queria_uma_bolsa_rs`

### Pelo browser (Swagger UI)

1. Confirme que a VM está `RUNNING` (`bash deploy/start.sh`).
2. Abra: http://34.132.112.29:8080/docs
3. O navegador pede user/senha — informe os de cima.
4. Clique em `POST /ask` → **Try it out**.
5. Cole um JSON no corpo (veja exemplos abaixo) → **Execute**.
6. A resposta aparece embaixo: `answer` (texto do modelo) + `citations` (lista com `doc_id`, `article_ref`, `page_start/end`, `url` do PDF original da ANEEL).

### Pelo terminal (curl)

```bash
curl -u "desafio-rag:queria_uma_bolsa_rs" \
  -X POST http://34.132.112.29:8080/ask \
  -H "Content-Type: application/json" \
  -d '{"question":"O que diz a REN 1000/2021?","top_k":6}'
```

### Schema da requisição

```json
{
  "question": "pergunta em português",
  "top_k": 6,
  "filters": {
    "situacao_doc": "NÃO CONSTA REVOGAÇÃO EXPRESSA",
    "has_table": true,
    "ano": 2021
  }
}
```

- `question` (obrigatório, 1–2000 chars).
- `top_k` (opcional, 1–50, default 6) — chunks retornados após reranker.
- `filters` (opcional) — filtro server-side sobre payload. Aceita `ano` (int), `situacao_doc` (`"REVOGADA"`, `"NÃO CONSTA REVOGAÇÃO EXPRESSA"`, `"SUSPENSA"`, ...), `has_table` (bool), `has_revoked` (bool), `doc_id` (str), `tipo_pdf` (str).

### Schema da resposta

```json
{
  "question": "...",
  "answer": "texto com citações embutidas [doc_id | art X]",
  "model": "gemini-2.5-flash",
  "citations": [
    {
      "doc_id": "ren20211000__e5fb7e538d",
      "article_ref": "Art. 356. A suspensão do fornecimento...",
      "page_start": 160,
      "page_end": 161,
      "situacao_doc": "NÃO CONSTA REVOGAÇÃO EXPRESSA",
      "url": "https://www2.aneel.gov.br/cedoc/ren20211000.pdf",
      "score": 0.94
    }
  ]
}
```

---

## 3. Exemplos de perguntas

O sistema cobre legislação ANEEL dos anos **2016, 2021 e 2022**: Resoluções Normativas (REN), Despachos (DSP/ADSP), Portarias (PRT/APRT), Resoluções Autorizativas, Notas Técnicas, Decisões.

### 3.1 — Perguntas gerais

```json
{"question":"O que diz a REN 1000/2021 sobre a prestação dos serviços de distribuição de energia?"}
```

```json
{"question":"Quais sao os prazos para religacao apos suspensao por inadimplencia segundo a ANEEL?"}
```

```json
{"question":"O que e geracao distribuida na REN 482/2012?"}
```

### 3.2 — Dados de tabelas

Bandeiras tarifárias, limites de potência, tarifas, prazos — dados estruturados:

```json
{"question":"Quais sao os valores adicionais das bandeiras tarifarias verde, amarela e vermelha definidos pela ANEEL?"}
```

```json
{"question":"Quais os limites de potencia para microgeracao e minigeracao distribuida?","top_k":8}
```

### 3.3 — Leis revogadas

O sistema reconhece documentos `REVOGADA` e contextualiza historicamente:

```json
{"question":"O que dizia a REN 414/2010 sobre suspensao do fornecimento de energia por inadimplencia? Essa norma ainda esta vigente?"}
```

### 3.4 — Identificador específico (número + ano)

Quando você cita o número do documento, o retriever usa *identifier lookup*:

```json
{"question":"O que estabelece o Despacho ANEEL n. 1442 de 2021?"}
```

```json
{"question":"Resumo do Despacho 2718 de 2021"}
```

### 3.5 — Artigos específicos

```json
{"question":"O que diz o art. 128 da REN 414/2010 sobre débitos pretéritos?"}
```

```json
{"question":"O que estabelece o art. 356 da REN 1000/2021?"}
```

### 3.6 — Usando filtros

Apenas normas vigentes:

```json
{
  "question":"Regras de qualidade do serviço de distribuição",
  "filters": {"situacao_doc":"NÃO CONSTA REVOGAÇÃO EXPRESSA"}
}
```

Apenas de 2022:

```json
{
  "question":"Mudanças regulatórias em geração distribuída",
  "filters": {"ano": 2022}
}
```

Apenas chunks com tabela:

```json
{
  "question":"Tarifa de uso do sistema de distribuição",
  "filters": {"has_table": true}
}
```

---

## 4. Como o RAG funciona (alto nível)

1. **Embed query** — BGE-M3 gera vetor denso (1024d) + vetor esparso.
2. **Busca híbrida** no Qdrant:
   - dense top-40 (similaridade cosseno)
   - sparse top-40 (BM25-style lexical)
   - **identifier lookup** se a query contém `número+ano` (ex: `1442` + `2021` → filtra pelos docs cujo arquivo casa).
3. **Fusão RRF** (Reciprocal Rank Fusion) dos três rankings → 20 candidatos.
4. **Reranker** BGE-reranker-v2-m3 pontua cada `(query, chunk)` com metadata `[arquivo: ...]` prefixada.
5. **Penalizações/boosts**:
   - chunks de ementa (pg 0) × 0.85
   - chunks vindos do identifier lookup × 1.5
6. **Top-k final** vai para o Gemini 2.5 Flash (Vertex AI) com prompt que instrui a citar `[doc_id | art/pg]`.

---

## 5. Tempo de resposta esperado

| Cenário | Tempo |
|---|---|
| `/ask` warm (modelos carregados) | 3–12 s |
| `/ask` após `deploy/start.sh` (warm-up já rodou) | 3–12 s |
| `/ask` primeira chamada após reboot sem warm-up | 1–3 min (carrega BGE-M3 + reranker + doc_index) |
| `/health` | < 200 ms |

---

## 6. Solução de problemas

| Sintoma | Causa provável | Fix |
|---|---|---|
| `HTTP 401` mesmo com credentials | Senha errada, caracteres especiais sem escape | Use `-u "user:pass"` no curl, browser trata automaticamente |
| `timed out` após ~5 min | Cold start — modelos ainda carregando | Rode `bash deploy/start.sh` para aquecer, tente de novo |
| `connection refused` | VM parada | `bash deploy/start.sh` |
| `"não consta no contexto"` em pergunta óbvia | Query muito genérica ou chunk não recuperado | Adicione o número/ano do documento na pergunta, ou aumente `top_k` |
| Resposta mostra `~~texto tachado~~` | Artefato de parsing de PDF | Cosmético; o conteúdo está correto. Re-embed limparia (~12h CPU) |

Logs da VM:

```bash
gcloud compute ssh aneel-rag-vm --zone=us-central1-a \
  --command="sudo docker logs --tail 100 aneel-qa"
```

---

## 7. Custo

Com `e2-standard-4` + 30GB boot + 50GB SSD:

- **Ligada 24/7**: ~US$ 50/mês de compute + US$ 8/mês de disco.
- **Desligada**: só ~US$ 8/mês de disco.
- **Vertex AI Gemini 2.5 Flash**: ~US$ 0,10 / 1M tokens de input. Uma `/ask` típica = ~3–8k tokens.

Hábito recomendado: `deploy/start.sh` antes da sessão → benchmark → `deploy/stop.sh`.
