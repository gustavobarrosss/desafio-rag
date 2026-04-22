# Deploy ANEEL RAG na GCP

Passo-a-passo operacional para subir o sistema nos dois projetos GCP:

- `desafio-rag` — INFRA (VM, GCS, Artifact Registry, Vertex Custom Job de embed)
- `bionic-medley-489719-t5` — LLM (Vertex AI Gemini)

## Pré-requisitos

- `gcloud` CLI autenticado (`gcloud auth login`) com acesso de owner/editor nos dois projetos.
- Docker local para build das imagens.

## Variáveis usadas nos comandos

```bash
export INFRA_PROJECT=desafio-rag
export LLM_PROJECT=bionic-medley-489719-t5
export REGION=us-central1
export ZONE=us-central1-a
export BUCKET=aneel-rag-data-desafio-rag
export AR_REPO=aneel
export AR_HOST=${REGION}-docker.pkg.dev
export ARTIFACT_REGISTRY=${AR_HOST}/${INFRA_PROJECT}/${AR_REPO}
export IMAGE_TAG=latest
export VM_NAME=aneel-rag-vm
export SECRET_NAME=vertex-llm-sa-key
```

---

## Fase 1 — Preparação GCP (manual, uma vez)

### Projeto INFRA (`desafio-rag`)

```bash
gcloud config set project ${INFRA_PROJECT}

gcloud services enable \
    compute.googleapis.com \
    storage.googleapis.com \
    artifactregistry.googleapis.com \
    aiplatform.googleapis.com \
    secretmanager.googleapis.com \
    logging.googleapis.com

# Bucket de dados
gsutil mb -l ${REGION} -b on gs://${BUCKET}

# Upload metadados JSON
gsutil -m cp dados_grupo_estudos/*.json gs://${BUCKET}/metadata/

# Artifact Registry
gcloud artifacts repositories create ${AR_REPO} \
    --repository-format=docker \
    --location=${REGION}

# Secret com a chave da SA LLM
gcloud secrets create ${SECRET_NAME} \
    --data-file=bionic-medley-489719-t5-00bd941e5a30.json

# Acesso da SA Infra ao secret
INFRA_SA=desafio-rag@${INFRA_PROJECT}.iam.gserviceaccount.com
gcloud secrets add-iam-policy-binding ${SECRET_NAME} \
    --member="serviceAccount:${INFRA_SA}" \
    --role="roles/secretmanager.secretAccessor"

# Roles da SA Infra no projeto
for ROLE in \
    roles/storage.objectAdmin \
    roles/artifactregistry.reader \
    roles/aiplatform.user \
    roles/logging.logWriter \
    roles/monitoring.metricWriter; do
    gcloud projects add-iam-policy-binding ${INFRA_PROJECT} \
        --member="serviceAccount:${INFRA_SA}" \
        --role="${ROLE}"
done
```

### Projeto LLM (`bionic-medley-489719-t5`)

```bash
gcloud config set project ${LLM_PROJECT}
gcloud services enable aiplatform.googleapis.com

LLM_SA=71991918385-compute@developer.gserviceaccount.com
gcloud projects add-iam-policy-binding ${LLM_PROJECT} \
    --member="serviceAccount:${LLM_SA}" \
    --role="roles/aiplatform.user"
```

Voltar para o projeto infra:

```bash
gcloud config set project ${INFRA_PROJECT}
```

---

## Fase 2 — Imagens Docker

```bash
# Auth Docker
gcloud auth configure-docker ${AR_HOST} --quiet

# Build + push (a partir da raiz do repo)
docker build -f docker/Dockerfile.pipeline -t ${ARTIFACT_REGISTRY}/pipeline:${IMAGE_TAG} .
docker build -f docker/Dockerfile.qa       -t ${ARTIFACT_REGISTRY}/qa:${IMAGE_TAG}       .
docker build -f docker/Dockerfile.embed    -t ${ARTIFACT_REGISTRY}/embed:${IMAGE_TAG}    .

docker push ${ARTIFACT_REGISTRY}/pipeline:${IMAGE_TAG}
docker push ${ARTIFACT_REGISTRY}/qa:${IMAGE_TAG}
docker push ${ARTIFACT_REGISTRY}/embed:${IMAGE_TAG}
```

---

## Fase 3 — VM

O `deploy/vm-startup.sh` lê vários atributos da instance metadata. O compose de produção também é passado como atributo (base64/texto) para simplificar — assim a VM não precisa fazer git clone.

```bash
# Passa o compose file como metadado da instância
COMPOSE_CONTENT=$(cat deploy/docker-compose.prod.yml)

gcloud compute instances create ${VM_NAME} \
    --project=${INFRA_PROJECT} \
    --zone=${ZONE} \
    --machine-type=e2-medium \
    --image-family=debian-12 \
    --image-project=debian-cloud \
    --boot-disk-size=30GB \
    --create-disk=name=aneel-data,size=50,type=pd-ssd,device-name=aneel-data \
    --service-account=${INFRA_SA} \
    --scopes=cloud-platform \
    --tags=aneel-qa \
    --metadata-from-file=startup-script=deploy/vm-startup.sh \
    --metadata=\
gcs-bucket=${BUCKET},\
artifact-registry=${ARTIFACT_REGISTRY},\
image-tag=${IMAGE_TAG},\
vertex-llm-secret-name=${SECRET_NAME},\
gcp-infra-project=${INFRA_PROJECT},\
gcp-llm-project=${LLM_PROJECT},\
vertexai-location=${REGION},\
qdrant-collection=aneel_legis,\
docker-compose-prod="${COMPOSE_CONTENT}"
```

> **Nota:** o disco SSD `aneel-data` precisa ser formatado e montado em `/var/lib/aneel` na primeira vez — ajuste o startup script se quiser automatizar. Para simplicidade, pode-se usar o disco boot (30GB é suficiente para o `state.sqlite`).

Firewall para permitir IAP chegar na porta 8080:

```bash
gcloud compute firewall-rules create allow-iap-qa \
    --network=default \
    --direction=INGRESS \
    --action=ALLOW \
    --rules=tcp:8080 \
    --source-ranges=35.235.240.0/20 \
    --target-tags=aneel-qa
```

---

## Fase 4 — Ingestão inicial

SSH na VM e rode os stages de batch dentro do container pipeline:

```bash
gcloud compute ssh ${VM_NAME} --zone=${ZONE}

# Dentro da VM
IMG=${ARTIFACT_REGISTRY}/pipeline:${IMAGE_TAG}
docker pull $IMG

# Smoke test
docker run --rm \
    -v /var/secrets:/var/secrets:ro \
    -v /mnt/gcs:/mnt/gcs \
    -v /var/lib/aneel:/var/lib/aneel \
    --env-file /etc/aneel/qa.env \
    -e VERTEX_LLM_CREDENTIALS_PATH=/var/secrets/vertex-llm-sa.json \
    $IMG init

docker run --rm [...as above] $IMG download --limit 100
docker run --rm [...as above] $IMG parse
docker run --rm [...as above] $IMG vision
docker run --rm [...as above] $IMG chunk
```

### Embedding via Vertex Custom Job

```bash
# Descobre IP interno da VM
VM_INTERNAL_IP=$(gcloud compute instances describe ${VM_NAME} --zone=${ZONE} \
    --format='value(networkInterfaces[0].networkIP)')

# Renderiza o YAML (envsubst ou sed)
envsubst < deploy/embed-job.yaml > /tmp/embed-job.yaml

gcloud ai custom-jobs create \
    --region=${REGION} \
    --display-name=aneel-embed \
    --config=/tmp/embed-job.yaml

# Acompanhar
gcloud ai custom-jobs list --region=${REGION}
gcloud ai custom-jobs stream-logs <JOB_ID> --region=${REGION}
```

> A VM precisa ter uma regra de firewall permitindo a subnet da Vertex AI chegar na porta 6333 interna. Alternativa: expor Qdrant via load balancer interno.

---

## Fase 5 — Validação

```bash
# 1) Health do QA (via IAP)
TOKEN=$(gcloud auth print-identity-token)
curl -H "Authorization: Bearer ${TOKEN}" \
    https://<IAP-URL>/health

# 2) Pergunta
curl -X POST -H "Authorization: Bearer ${TOKEN}" \
    -H "Content-Type: application/json" \
    https://<IAP-URL>/ask \
    -d '{"question":"Qual o prazo para revisão tarifária?"}'

# 3) Contagem no Qdrant (SSH na VM)
curl http://localhost:6333/collections/aneel_legis
```

---

## Operação diária

```bash
# Pausar (para cobrança de CPU, mantém disco)
gcloud compute instances stop ${VM_NAME} --zone=${ZONE}

# Retomar
gcloud compute instances start ${VM_NAME} --zone=${ZONE}

# Atualizar imagem QA após alteração de código
docker build -f docker/Dockerfile.qa -t ${ARTIFACT_REGISTRY}/qa:${IMAGE_TAG} .
docker push ${ARTIFACT_REGISTRY}/qa:${IMAGE_TAG}
gcloud compute ssh ${VM_NAME} --zone=${ZONE} --command='cd /opt/aneel-rag && docker compose -f docker-compose.prod.yml pull qa && docker compose -f docker-compose.prod.yml up -d qa'

# Destruir tudo
gcloud compute instances delete ${VM_NAME} --zone=${ZONE}
gsutil -m rm -r gs://${BUCKET}   # opcional
gcloud artifacts repositories delete ${AR_REPO} --location=${REGION}  # opcional
```

---

## Teste local (antes de subir)

Valida o QA end-to-end usando Qdrant local e a mesma imagem que vai pra produção:

```bash
cp .env.example .env
# edite .env com os dados do seu ambiente (principalmente GCP_LLM_PROJECT
# e VERTEX_LLM_CREDENTIALS_PATH apontando para a key baixada localmente)

docker compose build qa
docker compose up -d qdrant qa

curl localhost:8080/health
curl -X POST localhost:8080/ask \
    -H "Content-Type: application/json" \
    -d '{"question":"teste"}'
```
