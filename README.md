# COMO USAR

Guia rápido para subir o servidor na GCP, parar quando não estiver usando, e consultar o endpoint `/ask` com exemplos prontos.

## FAZER TESTES: http://34.28.189.251:8501 (RODANDO EM UMA VM NA GCP 24/7)

## FASTAPI: 34.28.189.251:8080/docs

login: desafio-rag
senha: queria_uma_bolsa_rs

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

IP externo é **efêmero** — muda a cada `start`. `deploy/start.sh` imprime o IP atual ao final. Ou consulte direto:

```bash
gcloud compute instances describe aneel-rag-vm \
  --zone=us-central1-a --project=desafio-rag \
  --format="value(networkInterfaces[0].accessConfigs[0].natIP)"
```

Endpoint base: `http://<IP_EXTERNO>:8080`

> **⚠️ HTTP sem TLS.** Senha trafega em texto claro. OK para benchmark/demo de curto prazo — rotacione depois.

### Credenciais

- **user:** `desafio-rag`
- **senha:** `queria_uma_bolsa_rs`

### Pelo browser (Swagger UI)

1. Confirme que a VM está `RUNNING` (`bash deploy/start.sh`) e pegue o IP no output.
2. Abra: `http://<IP_EXTERNO>:8080/docs`
3. O navegador pede user/senha — informe os de cima.
4. Clique em `POST /ask` → **Try it out**.
5. Cole um JSON no corpo (veja exemplos abaixo) → **Execute**.
6. A resposta aparece embaixo: `answer` (texto do modelo) + `citations` (lista com `doc_id`, `article_ref`, `page_start/end`, `url` do PDF original da ANEEL).

### Pelo terminal (curl)

```bash
IP=$(gcloud compute instances describe aneel-rag-vm \
  --zone=us-central1-a --project=desafio-rag \
  --format="value(networkInterfaces[0].accessConfigs[0].natIP)")

curl -u "desafio-rag:queria_uma_bolsa_rs" \
  -X POST "http://${IP}:8080/ask" \
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
4. **Reranker** pontua cada `(query, chunk)` com metadata `[arquivo: ...]` prefixada:
   - **Cohere `rerank-multilingual-v3.0`** quando `COHERE_API_KEY` está setada (default em produção — rápido, sem GPU, com retry/backoff em 429).
   - Fallback **BGE-reranker-v2-m3** local se a chave não existir.
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
- **Vertex AI Gemini 2.5 Flash**: ~US$ 0,15 / 1M tokens de input · US$ 0,60 / 1M output. Uma `/ask` típica = ~3–8k tokens input.
- **Cohere Rerank v3 multilingual**: cobrança por *search unit* (uma query até 100 docs). Volume do benchmark é desprezível.

Hábito recomendado: `deploy/start.sh` antes da sessão → benchmark → `deploy/stop.sh`.


# ANEEL RAG — Pipeline de Ingestão e Consulta

Sistema de RAG (*Retrieval-Augmented Generation*) sobre legislação ANEEL (2016, 2021, 2022). Faz download, parse, chunking, embedding e indexação de ~27 mil PDFs para consulta semântica via LLM.

> **Branch `gcp`**: LLM migrado de OpenRouter (Gemma free) para **Google Vertex AI (Gemini 2.5 Flash)**, consumindo créditos GCP.

## Arquitetura

```
Metadata JSONs
     │
     ▼
  [init] → state.sqlite (18 688 docs / 27 039 PDFs)
     │
     ▼
[download] → curl_cffi + impersonate=chrome → data/pdfs/
     │
     ▼
  [parse] → PyMuPDF / pdfplumber / Camelot → data/parsed/
     │   (router classifica: digital / mixed / vision_heavy)
     ▼
 [vision] → Vertex AI Gemini 2.5 Flash (OCR para páginas com imagens)
     │
     ▼
  [chunk] → tokens ≤900, overlap 100 → data/chunks/
     │
     ▼
  [embed] → BGE-M3 (dense 1024d) + BM25 sparse → Qdrant
     │
     ▼
    [qa] → retriever híbrido (RRF) + reranker + Gemini 2.5 Flash → resposta
```

## Requisitos

- Python 3.11+
- Docker (para Qdrant)
- GPU recomendada para embedding (CPU funciona, mais lento)
- Projeto GCP com **Vertex AI API** habilitada e créditos associados

## Instalação

```bash
git clone https://github.com/gustavobarrosss/desafio-rag
cd desafio-rag
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/Mac
source .venv/bin/activate

pip install -r requirements.txt
pip install curl_cffi   # necessário para bypass Cloudflare
```

## Configuração

Copie `.env.example` para `.env` e preencha com os dados do seu projeto GCP:

```bash
cp .env.example .env
```

```env
# Vertex AI (GCP)
GOOGLE_CLOUD_PROJECT=seu-projeto-gcp
GOOGLE_CLOUD_LOCATION=us-central1
VERTEXAI_MODEL=gemini-2.5-flash
VERTEXAI_FALLBACK_MODEL=gemini-2.5-flash-lite
VERTEXAI_RPM=60
VERTEXAI_RPD=1000

# Autenticação — escolha uma das opções:
# Opção A (recomendado): gcloud auth application-default login
# Opção B: GOOGLE_APPLICATION_CREDENTIALS=/caminho/para/service-account.json

QDRANT_URL=http://localhost:6333
QDRANT_COLLECTION=aneel_legis

EMBED_MODEL=BAAI/bge-m3
EMBED_DEVICE=cuda   # ou cpu
EMBED_BATCH=32
```

### Autenticação GCP

**Opção A — gcloud CLI:**
```bash
gcloud auth application-default login
gcloud config set project SEU_PROJETO_GCP
```

**Opção B — Service Account JSON:**
1. Console GCP → IAM → Contas de serviço → criar conta com papel **Vertex AI User**
2. Gerar chave JSON e salvar localmente
3. Definir `GOOGLE_APPLICATION_CREDENTIALS=/caminho/chave.json` no `.env`

## Subindo o Qdrant

```bash
docker compose up -d
```

Isso sobe `qdrant/qdrant:v1.12.4` na porta `6333` com dados persistidos em `./qdrant_storage/`.

```bash
curl http://localhost:6333/healthz
```

## Rodando a Pipeline

### Pipeline completa (recomendado)

```bash
python run_pipeline.py all
```

Executa em sequência: `init → download → parse → vision → chunk → embed`.

**Com limite** (útil para testar):
```bash
python run_pipeline.py all --limit 100
```

A pipeline é idempotente — documentos já processados são pulados. Erros são re-tentados na próxima execução.

### Etapas individuais

```bash
# 1. Carrega metadados nos JSONs → state.sqlite
python run_pipeline.py init

# 2. Baixa PDFs (Cloudflare-safe)
python run_pipeline.py download [--limit N]

# 3. Parse rápido (PyMuPDF + pdfplumber + Camelot)
python run_pipeline.py parse [--limit N]

# 4. Parse por visão — Gemini 2.5 Flash via Vertex AI
python run_pipeline.py vision [--limit N]

# 5. Chunking dos textos
python run_pipeline.py chunk [--limit N]

# 6. Embedding + upsert no Qdrant
python run_pipeline.py embed [--limit N]
```

### Status da pipeline

```bash
python run_pipeline.py status
```

## Consulta

```bash
python run_pipeline.py qa "Qual o prazo para revisão tarifária da distribuidora?"
```

Saída:
```json
{
  "answer": "...",
  "model": "gemini-2.5-flash",
  "citations": [{"doc_id": "...", "article_ref": "art. 10", "score": 0.91}]
}
```

## Avaliação

```bash
python run_pipeline.py evaluate data/eval/benchmark.jsonl [--top-k 6]
```

## Estimativa de Custo (Vertex AI)

Corpus completo: **18.688 documentos / 27.039 PDFs / ~216.000 páginas estimadas**.

| Etapa | Custo estimado |
|---|---|
| Parse digital (PyMuPDF) | $0,00 |
| Vision OCR — ~75k páginas (35%) | ~$22–$75* |
| Embedding BGE-M3 (local) | $0,00 |
| QA por consulta | ~$0,001/pergunta |

\* Faixa depende do uso de *thinking tokens* do Gemini 2.5 Flash ($3,50/M tokens). Com `thinking_budget=0` o custo fica em ~$22.

Preços de referência Vertex AI Gemini 2.5 Flash: $0,15/M tokens input · $0,60/M tokens output · $3,50/M tokens thinking.

## Estrutura do projeto

```
desafio-rag/
├── dados_grupo_estudos/          # metadados ANEEL (2016/2021/2022)
├── data/
│   ├── pdfs/                     # PDFs baixados
│   ├── parsed/                   # texto extraído por doc
│   ├── chunks/                   # chunks prontos para embed
│   ├── eval/                     # benchmarks de avaliação
│   └── state.sqlite              # checkpoint de cada etapa
├── src/
│   ├── config.py                 # configurações e defaults (VisionConfig → Vertex AI)
│   ├── state.py                  # state machine (sqlite)
│   ├── download.py               # downloader async (curl_cffi)
│   ├── classify.py               # router digital/mixed/vision
│   ├── parse_digital.py          # PyMuPDF + pdfplumber + Camelot
│   ├── parse_vision.py           # OCR via Gemini 2.5 Flash (Vertex AI)
│   ├── parse_runner.py           # orquestrador parse
│   ├── parse_vision_runner.py    # orquestrador vision
│   ├── chunker.py                # chunking por tokens
│   ├── embed.py                  # BGE-M3 embedding
│   ├── ingest.py                 # upsert Qdrant
│   ├── retriever.py              # busca híbrida (RRF) + reranker BGE
│   ├── qa.py                     # geração de resposta (Gemini via Vertex AI)
│   └── evaluate.py               # harness de avaliação (LLM-as-judge)
├── run_pipeline.py               # CLI principal
├── docker-compose.yml
├── requirements.txt
└── .env.example
```

## Notas

- **Download**: site ANEEL usa Cloudflare com inspeção de TLS (JA3). `curl_cffi` com `impersonate="chrome"` é obrigatório — `requests`/`httpx` recebem 403.
- **Vision**: etapa opcional mas recomendada. Páginas classificadas como `vision_heavy` ficam com parse incompleto se o Vertex AI não estiver configurado.
- **SDK**: usa `google-genai` (unified SDK) com `vertexai=True`, não o SDK legado `vertexai.generative_models` (deprecado em jun/2025).
- **Retries**: falhas em qualquer etapa ficam com `status='error'` no sqlite e são re-tentadas automaticamente no próximo `run_pipeline.py`.
- **Escala**: pipeline completa com 27K docs leva várias horas dependendo de CPU/GPU e largura de banda.
