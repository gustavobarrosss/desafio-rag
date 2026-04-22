# Testando a arquitetura ANEEL RAG em produção

Este documento explica como verificar, testar e observar a stack completa
rodando no GCP.

---

## Visão geral da arquitetura

```
  Você (laptop)
       │
       │  IAP tunnel (porta 8081 local → 8080 VM)
       ▼
┌──────────────────────────────────────────────────┐
│ Compute Engine: aneel-rag-vm (e2-medium)         │
│ ─────────────────────────────────────────────    │
│  /mnt/gcs          ← gcsfuse mount do bucket     │
│  /var/lib/aneel    ← SSD persistente:            │
│     qdrant/        ← storage do Qdrant           │
│     hf_cache/      ← modelo BGE-M3 cached        │
│     state.sqlite   ← tracking do pipeline        │
│  /var/secrets      ← chave SA do Vertex LLM      │
│                                                  │
│  Docker Compose:                                 │
│    ┌────────────┐    ┌──────────────────┐        │
│    │ aneel-qa   │───▶│ aneel-qdrant     │        │
│    │ :8080      │    │ :6333 (loopback) │        │
│    │ (FastAPI)  │    └──────────────────┘        │
│    └─────┬──────┘                                │
│          │                                       │
└──────────┼───────────────────────────────────────┘
           │
           │ Vertex AI API (cross-project)
           ▼
   ┌─────────────────────┐    ┌──────────────────┐
   │ bionic-medley-...   │    │ desafio-rag      │
   │ - Gemini 2.5 Flash  │    │ - GCS bucket     │
   │ - Gemini 2.5 Pro    │    │ - Artifact Reg.  │
   └─────────────────────┘    │ - Secret Manager │
                              └──────────────────┘
```

**Projetos**: `desafio-rag` hospeda infra (VM, GCS, imagens Docker, secret).
`bionic-medley-489719-t5` hospeda o LLM (Gemini via Vertex AI).

---

## Pré-requisitos

- `gcloud` CLI autenticado como `henriquegustavo@discente.ufg.br`
- Projeto ativo: `gcloud config set project desafio-rag`
- `curl`, `python` (para formatar JSON)

---

## 1. Verificar infraestrutura GCP

### VM e containers

```bash
# VM está rodando?
gcloud compute instances describe aneel-rag-vm --zone=us-central1-a \
  --format="value(status,machineType.basename(),networkInterfaces[0].networkIP)"
# Esperado: RUNNING  e2-medium  10.x.x.x

# Serial console (últimas linhas do boot/startup)
gcloud compute instances get-serial-port-output aneel-rag-vm \
  --zone=us-central1-a | tail -30
```

### GCS bucket

```bash
# Listar conteúdo
gsutil ls gs://aneel-rag-data-desafio-rag/

# Ver PDFs baixados (smoke: 40-70 docs)
gsutil ls gs://aneel-rag-data-desafio-rag/pdfs/ | wc -l
```

### Artifact Registry (imagens)

```bash
gcloud artifacts docker images list \
  us-central1-docker.pkg.dev/desafio-rag/aneel \
  --format="table(IMAGE,TAGS,CREATE_TIME)"
```

### Secret Manager

```bash
gcloud secrets list --project=desafio-rag
# Deve listar: vertex-llm-sa-key
```

---

## 2. Testar o QA end-to-end (principal caso de uso)

### 2.1 Abrir IAP tunnel

O QA está exposto apenas via IAP (sem IP público). Abra um túnel local:

```bash
gcloud compute start-iap-tunnel aneel-rag-vm 8080 \
  --local-host-port=localhost:8081 \
  --zone=us-central1-a
```

> Deixe este comando rodando em um terminal separado (ele fica em foreground).

### 2.2 Health check

```bash
curl http://localhost:8081/health
# Esperado: {"status":"ok"}
```

### 2.3 Fazer pergunta (`/ask`)

```bash
curl -s -X POST http://localhost:8081/ask \
  -H "Content-Type: application/json" \
  -d '{"question":"O que diz a REN 1000 sobre distribuição de energia?","top_k":8}' \
  | python -m json.tool
```

**Primeira chamada pós-reboot**: leva ~3-15 min (QA baixa modelo BGE-M3
~2.3GB na primeira execução). Chamadas seguintes: ~5-15s.

**Resposta esperada** — JSON com:
- `question`: eco da pergunta
- `answer`: texto do Gemini com marcadores `[1 | art. X]`
- `model`: `gemini-2.5-flash`
- `citations`: lista com `doc_id`, `article_ref`, `page_start/end`, `url`
  (link direto para o PDF original na ANEEL)

---

## 3. Verificar o Qdrant diretamente

Via SSH + port-forward (já funciona via o mesmo tunnel do QA se você
abrir outra porta):

```bash
# Em outro terminal, novo tunnel pra Qdrant:
gcloud compute start-iap-tunnel aneel-rag-vm 6333 \
  --local-host-port=localhost:6333 \
  --zone=us-central1-a
```

> Isso exige uma regra de firewall adicional pra 6333 se ainda não existir.
> Alternativa: acessar via SSH direto (próxima seção).

```bash
# Coleção existe?
curl http://localhost:6333/collections/aneel_legis

# Quantos pontos?
curl http://localhost:6333/collections/aneel_legis | python -m json.tool | grep points_count
# Esperado no smoke: "points_count": 202
```

---

## 4. SSH direto na VM (debug)

```bash
gcloud compute ssh aneel-rag-vm --zone=us-central1-a
```

### Comandos úteis dentro da VM

```bash
# Status dos containers
sudo docker ps
# Esperado: aneel-qa Up ... (healthy), aneel-qdrant Up ...

# Logs do QA (últimas 50 linhas)
sudo docker logs --tail 50 aneel-qa

# Logs do Qdrant
sudo docker logs --tail 30 aneel-qdrant

# Uso de disco
df -h /
# /dev/sda1  30G  ~15G  ~15G  50%

# Memória
free -h

# Config env do QA
sudo cat /etc/aneel/qa.env

# Compose file ativo
sudo cat /opt/aneel-rag/docker-compose.prod.yml

# Verificar mount GCS
mountpoint /mnt/gcs && ls /mnt/gcs/aneel-rag-data/pdfs/ | head -5
```

---

## 5. Rodar ingestão (smoke ou completa)

### Smoke (40 docs locais já no GCS → embed → Qdrant)

```bash
# 1. Pular download (já no bucket). Ir direto para embed:
sudo docker run --rm --name aneel-embed \
  --network aneel-rag_default \
  --env-file /etc/aneel/qa.env \
  -e QDRANT_URL=http://qdrant:6333 \
  -e EMBED_DEVICE=cpu \
  -e DATA_DIR=/mnt/gcs/aneel-rag-data \
  -e STATE_DB_PATH=/var/lib/aneel/state.sqlite \
  -e HF_HUB_DISABLE_XET=1 \
  -v /var/lib/aneel:/var/lib/aneel \
  -v /var/lib/aneel/hf_cache:/models \
  -v /mnt/gcs:/mnt/gcs:ro \
  us-central1-docker.pkg.dev/desafio-rag/aneel/pipeline:latest embed
```

Duração: ~23 min CPU para 40 docs (primeira execução; depois disso a BGE-M3
fica cached em `/var/lib/aneel/hf_cache`).

Resultado: `{"pending": 40, "ok": 40, "errors": 0, "points": 202}`.

### Completa (27K docs — requer desbloqueio Cloudflare)

Veja [README.md](README.md) e comentários no código. Requer proxy residencial
(download de GCP é bloqueado por Cloudflare com 403) ou download local em lote.

---

## 6. Monitorar custo

```bash
# Billing atual (aproximado)
gcloud billing accounts list
gcloud alpha billing budgets list --billing-account=<ID>

# Consumo de recursos (top):
# - Compute Engine (VM 24/7): ~$25/mês
# - Cloud Storage: ~$0.20/mês por 10GB
# - Artifact Registry: ~$0.50/mês (imagens)
# - Secret Manager: grátis (<6 acessos/mês)
# - Vertex AI: variável por tokens (Gemini Flash ~ $0.10 / 1M input tokens)
```

---

## 7. Problemas conhecidos e soluções rápidas

| Sintoma | Causa | Solução |
|---|---|---|
| `/ask` retorna `ImportError: is_torch_fx_available` | Imagem QA com transformers ≥4.49 | Rebuild imagem com pin `transformers<4.49` no requirements.txt, push, `docker compose up -d qa` |
| `/ask` trava por horas na 1ª chamada | XET protocol bugado no download BGE-M3 | Env `HF_HUB_DISABLE_XET=1` no container |
| `tunnel failed to connect to backend` | Firewall IAP faltando | `gcloud compute firewall-rules create allow-iap-8080 --source-ranges=35.235.240.0/20 --allow=tcp:8080` |
| Downloads de ANEEL retornam 403 | Cloudflare bloqueia IPs GCP | Usar proxy residencial ou baixar local e subir ao GCS |
| Startup script não termina | `gcsfuse-aneel.service` em `Type=forking` timeout | Já corrigido em `vm-startup.sh` para `Type=simple` + `--foreground` |
| SSH travando no Windows | `plink.exe` do gcloud com bug | `taskkill /F /IM plink.exe` e tentar novamente, ou usar Cloud Shell |

---

## 8. Referência rápida de arquivos

```
deploy/
├── docker-compose.prod.yml   # Serviços qa + qdrant em produção
├── vm-startup.sh             # Instala docker/gcsfuse, monta GCS, sobe containers
├── finish-startup.sh         # Script auxiliar (retomar startup após falha)
├── embed-job.yaml            # Vertex Custom Job (embed GPU - não usado ainda)
├── cloudbuild-qa.yaml        # Config do Cloud Build para rebuilds
├── README.md                 # Guia operacional completo do deploy
└── TESTING.md                # Este arquivo

docker/
├── Dockerfile.qa             # Imagem do FastAPI QA (CPU)
├── Dockerfile.pipeline       # Imagem do pipeline completo (download/parse/chunk/embed)
└── Dockerfile.embed          # Imagem GPU-only para Vertex Custom Job

src/                          # Código Python da stack
run_pipeline.py               # CLI: init/download/parse/chunk/embed/qa/all
```

---

## 9. Teste rápido em 3 comandos

Se tudo estiver no ar, isso basta para validar:

```bash
# Terminal 1 (deixe aberto):
gcloud compute start-iap-tunnel aneel-rag-vm 8080 \
  --local-host-port=localhost:8081 --zone=us-central1-a

# Terminal 2:
curl http://localhost:8081/health
curl -s -X POST http://localhost:8081/ask \
  -H "Content-Type: application/json" \
  -d '{"question":"Resumo da REN 1000"}' | python -m json.tool
```

Se o JSON retornar `answer` com texto + `citations` com URLs da ANEEL,
**a stack inteira está funcionando**.
