#!/usr/bin/env bash
# VM startup script for the ANEEL RAG service VM.
#
# Executed by Compute Engine on first boot (and re-run if `--metadata-from-file
# startup-script=...` is updated). Idempotent.
#
# Responsibilities:
#   1. Install Docker + Docker Compose plugin + gcsfuse
#   2. Mount the GCS data bucket at /mnt/gcs
#   3. Fetch the Vertex LLM SA key from Secret Manager and drop it in /var/secrets
#   4. Write /etc/aneel/qa.env from instance metadata
#   5. Authenticate Docker to Artifact Registry
#   6. docker compose up -d using deploy/docker-compose.prod.yml
#
# Required instance metadata (set via `gcloud compute instances add-metadata`):
#   gcs-bucket             → e.g. aneel-rag-data-desafio-rag
#   artifact-registry      → e.g. us-central1-docker.pkg.dev/desafio-rag/aneel
#   image-tag              → e.g. latest
#   vertex-llm-secret-name → e.g. vertex-llm-sa-key
#   gcp-infra-project      → e.g. desafio-rag
#   gcp-llm-project        → e.g. bionic-medley-489719-t5
#   vertexai-location      → e.g. us-central1
#   qdrant-collection      → e.g. aneel_legis

set -euo pipefail

log() { echo "[startup] $*"; }

META="http://metadata.google.internal/computeMetadata/v1/instance/attributes"
meta() { curl -fsS -H "Metadata-Flavor: Google" "${META}/$1"; }

GCS_BUCKET=$(meta gcs-bucket)
ARTIFACT_REGISTRY=$(meta artifact-registry)
IMAGE_TAG=$(meta image-tag || echo "latest")
VERTEX_SECRET_NAME=$(meta vertex-llm-secret-name)
GCP_INFRA_PROJECT=$(meta gcp-infra-project)
GCP_LLM_PROJECT=$(meta gcp-llm-project)
VERTEXAI_LOCATION=$(meta vertexai-location || echo "us-central1")
QDRANT_COLLECTION=$(meta qdrant-collection || echo "aneel_legis")

AR_HOST="${ARTIFACT_REGISTRY%%/*}"

log "bucket=${GCS_BUCKET} registry=${ARTIFACT_REGISTRY} tag=${IMAGE_TAG}"

# ---------- 1. packages ----------
if ! command -v docker >/dev/null 2>&1; then
    log "installing docker + compose plugin"
    apt-get update
    apt-get install -y ca-certificates curl gnupg lsb-release
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/debian/gpg \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
        > /etc/apt/sources.list.d/docker.list
    apt-get update
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    systemctl enable --now docker
fi

if ! command -v gcsfuse >/dev/null 2>&1; then
    log "installing gcsfuse"
    export GCSFUSE_REPO=gcsfuse-$(. /etc/os-release && echo "$VERSION_CODENAME")
    echo "deb [signed-by=/usr/share/keyrings/cloud.google.asc] https://packages.cloud.google.com/apt $GCSFUSE_REPO main" \
        > /etc/apt/sources.list.d/gcsfuse.list
    curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg \
        -o /usr/share/keyrings/cloud.google.asc
    apt-get update
    apt-get install -y gcsfuse
fi

# ---------- 2. directories ----------
mkdir -p /mnt/gcs /var/lib/aneel/qdrant /var/lib/aneel/hf_cache /var/secrets /etc/aneel
chmod 755 /mnt/gcs /var/lib/aneel
chmod 700 /var/secrets

# ---------- 3. gcsfuse mount (systemd unit, idempotent) ----------
if ! mountpoint -q /mnt/gcs; then
    log "mounting gs://${GCS_BUCKET} at /mnt/gcs"
    cat >/etc/systemd/system/gcsfuse-aneel.service <<EOF
[Unit]
Description=gcsfuse mount ${GCS_BUCKET}
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/gcsfuse --foreground -o allow_other --implicit-dirs ${GCS_BUCKET} /mnt/gcs
ExecStop=/bin/fusermount -u /mnt/gcs
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable --now gcsfuse-aneel.service
fi

# ---------- 4. fetch Vertex LLM SA key from Secret Manager ----------
log "fetching secret ${VERTEX_SECRET_NAME} from project ${GCP_INFRA_PROJECT}"
gcloud secrets versions access latest \
    --secret="${VERTEX_SECRET_NAME}" \
    --project="${GCP_INFRA_PROJECT}" \
    > /var/secrets/vertex-llm-sa.json
chmod 600 /var/secrets/vertex-llm-sa.json

# ---------- 5. env for the QA container ----------
cat >/etc/aneel/qa.env <<EOF
GCP_INFRA_PROJECT=${GCP_INFRA_PROJECT}
GCP_LLM_PROJECT=${GCP_LLM_PROJECT}
GOOGLE_CLOUD_PROJECT=${GCP_LLM_PROJECT}
GOOGLE_CLOUD_LOCATION=${VERTEXAI_LOCATION}
GCS_BUCKET=${GCS_BUCKET}
QDRANT_COLLECTION=${QDRANT_COLLECTION}
EOF
chmod 600 /etc/aneel/qa.env

# ---------- 6. docker auth + compose up ----------
log "authenticating docker to ${AR_HOST}"
gcloud auth configure-docker "${AR_HOST}" --quiet

COMPOSE_DIR=/opt/aneel-rag
mkdir -p "${COMPOSE_DIR}"
# In production you'd bake the compose file into the image or pull from repo;
# for simplicity we copy it from instance metadata (base64 attribute).
meta docker-compose-prod > "${COMPOSE_DIR}/docker-compose.prod.yml"

cat >"${COMPOSE_DIR}/.env" <<EOF
ARTIFACT_REGISTRY=${ARTIFACT_REGISTRY}
IMAGE_TAG=${IMAGE_TAG}
EOF

log "pulling images + starting containers"
cd "${COMPOSE_DIR}"
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d

log "startup complete"
