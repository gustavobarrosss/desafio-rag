# ANEEL RAG — Pipeline de Ingestão e Consulta

Sistema de RAG (*Retrieval-Augmented Generation*) sobre legislação ANEEL (2016, 2021, 2022). Faz download, parse, chunking, embedding e indexação de ~27 mil PDFs para consulta semântica via LLM.

## Arquitetura

```
Metadata JSONs
     │
     ▼
  [init] → state.sqlite (26 772 docs)
     │
     ▼
[download] → curl_cffi + impersonate=chrome → data/pdfs/
     │
     ▼
  [parse] → PyMuPDF / pdfplumber / Camelot → data/parsed/
     │   (router classifica: digital / mixed / vision_heavy)
     ▼
 [vision] → OpenRouter (Gemma) para páginas com imagens pesadas
     │
     ▼
  [chunk] → tokens ≤900, overlap 100 → data/chunks/
     │
     ▼
  [embed] → BGE-M3 (dense 1024d) + BM25 sparse → Qdrant
     │
     ▼
    [qa] → retriever híbrido (RRF) + reranker + LLM → resposta
```

## Requisitos

- Python 3.11+
- Docker (para Qdrant)
- GPU recomendada para embedding (CPU funciona, mais lento)
- Conta [OpenRouter](https://openrouter.ai) para etapa `vision`

## Instalação

```bash
git clone <repo>
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

Copie `.env.example` para `.env` e preencha:

```bash
cp .env.example .env
```

```env
OPENROUTER_API_KEY=sk-or-...   # obrigatório para etapa vision
OPENROUTER_MODEL=google/gemma-4-31b-it:free
OPENROUTER_RPM=20              # requests/min da sua tier

QDRANT_URL=http://localhost:6333
QDRANT_COLLECTION=aneel_legis

EMBED_MODEL=BAAI/bge-m3
EMBED_DEVICE=cuda              # ou cpu
EMBED_BATCH=32
```

## Subindo o Qdrant

```bash
docker compose up -d
```

Isso sobe `qdrant/qdrant:v1.12.4` na porta `6333` com dados persistidos em `./qdrant_storage/`.

Verificar:
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

# 4. Parse por visão (páginas com imagens pesadas)
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

Saída exemplo:
```json
{
  "download": {"ok": 24100, "pending": 2672, "error": 0},
  "parse":    {"ok": 24100, "pending": 0,    "error": 0},
  "chunk":    {"ok": 24100, "pending": 0,    "error": 0},
  "embed":    {"ok": 24100, "pending": 0,    "error": 0}
}
```

## Consulta

```bash
python run_pipeline.py qa "Qual o prazo para revisão tarifária da distribuidora?"
```

Saída:
```json
{
  "answer": "...",
  "model": "google/gemma-4-31b-it:free",
  "citations": ["doc_id_1", "doc_id_2"]
}
```

## Avaliação

```bash
python run_pipeline.py evaluate data/eval/benchmark.jsonl [--top-k 6]
```

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
│   ├── config.py                 # configurações e defaults
│   ├── state.py                  # state machine (sqlite)
│   ├── download.py               # downloader async (curl_cffi)
│   ├── classify.py               # router digital/mixed/vision
│   ├── parse_digital.py          # PyMuPDF + pdfplumber + Camelot
│   ├── parse_vision.py           # OCR via LLM multimodal
│   ├── parse_runner.py           # orquestrador parse
│   ├── parse_vision_runner.py    # orquestrador vision
│   ├── chunker.py                # chunking por tokens
│   ├── embed.py                  # BGE-M3 embedding
│   ├── ingest.py                 # upsert Qdrant
│   ├── retriever.py              # busca híbrida + reranker
│   └── qa.py                     # geração de resposta
├── run_pipeline.py               # CLI principal
├── docker-compose.yml
├── requirements.txt
└── .env.example
```

## Notas

- **Download**: site ANEEL usa Cloudflare com inspeção de TLS (JA3). `curl_cffi` com `impersonate="chrome"` é obrigatório — `requests`/`httpx` recebem 403.
- **Vision**: etapa opcional. Documentos classificados como `vision_heavy` ficam com parse incompleto se `OPENROUTER_API_KEY` não configurado.
- **Retries**: falhas em qualquer etapa ficam com `status='error'` no sqlite e são re-tentadas automaticamente no próximo `run_pipeline.py`.
- **Escala**: pipeline completa com 27K docs leva várias horas dependendo de CPU/GPU e largura de banda.
