#!/usr/bin/env bash
set -euo pipefail
mkdir -p /var/secrets /etc/aneel /opt/aneel-rag /var/lib/aneel/qdrant /var/lib/aneel/hf_cache
chmod 700 /var/secrets

gcloud secrets versions access latest --secret=vertex-llm-sa-key --project=desafio-rag > /var/secrets/vertex-llm-sa.json
chmod 600 /var/secrets/vertex-llm-sa.json

cat >/etc/aneel/qa.env <<EOF
GCP_INFRA_PROJECT=desafio-rag
GCP_LLM_PROJECT=bionic-medley-489719-t5
GOOGLE_CLOUD_PROJECT=bionic-medley-489719-t5
GOOGLE_CLOUD_LOCATION=us-central1
GCS_BUCKET=aneel-rag-data-desafio-rag
QDRANT_COLLECTION=aneel_legis
EOF
chmod 600 /etc/aneel/qa.env

curl -fsS -H "Metadata-Flavor: Google" http://metadata.google.internal/computeMetadata/v1/instance/attributes/docker-compose-prod > /opt/aneel-rag/docker-compose.prod.yml
cat >/opt/aneel-rag/.env <<EOF
ARTIFACT_REGISTRY=us-central1-docker.pkg.dev/desafio-rag/aneel
IMAGE_TAG=latest
EOF

gcloud auth configure-docker us-central1-docker.pkg.dev --quiet
cd /opt/aneel-rag
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
sleep 5
docker ps
