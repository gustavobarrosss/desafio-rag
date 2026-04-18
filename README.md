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
