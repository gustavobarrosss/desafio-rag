#!/usr/bin/env bash
# Start aneel-rag-vm, wait for containers, warm up models so /ask is fast.
# Usage: bash deploy/start.sh
set -euo pipefail

INSTANCE="aneel-rag-vm"
ZONE="us-central1-a"
PROJECT="desafio-rag"
WARMUP_QUERY='{"question":"teste de aquecimento do modelo","top_k":3}'

# --- colors + helpers ------------------------------------------------------
if [ -t 1 ]; then
  BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'; GRN=$'\033[32m'
  YLW=$'\033[33m'; BLU=$'\033[34m'; CYN=$'\033[36m'; RST=$'\033[0m'
else
  BOLD=""; DIM=""; RED=""; GRN=""; YLW=""; BLU=""; CYN=""; RST=""
fi

ts() { date +"%H:%M:%S"; }
log()   { printf "%s[%s]%s %s\n" "$DIM" "$(ts)" "$RST" "$*"; }
step()  { printf "\n%s[%s]%s %s%s%s\n" "$DIM" "$(ts)" "$RST" "$BOLD$BLU" "$*" "$RST"; }
ok()    { printf "%s[%s]%s %s✔%s %s\n" "$DIM" "$(ts)" "$RST" "$GRN" "$RST" "$*"; }
warn()  { printf "%s[%s]%s %s⚠%s %s\n" "$DIM" "$(ts)" "$RST" "$YLW" "$RST" "$*"; }
err()   { printf "%s[%s]%s %s✘%s %s\n" "$DIM" "$(ts)" "$RST" "$RED" "$RST" "$*" >&2; }

START_EPOCH=$(date +%s)
elapsed() { echo "$(( $(date +%s) - START_EPOCH ))s"; }

# --- 1. VM state -----------------------------------------------------------
step "1/5  Checking VM state  (${CYN}${INSTANCE}${RST} @ ${ZONE})"
STATUS=$(gcloud compute instances describe "$INSTANCE" \
  --zone="$ZONE" --project="$PROJECT" \
  --format="value(status)" 2>/dev/null || echo "NOT_FOUND")
log "  current status: ${BOLD}${STATUS}${RST}"

case "$STATUS" in
  RUNNING)
    ok "VM already RUNNING — skipping start."
    ;;
  TERMINATED|STOPPED|SUSPENDED)
    log "  issuing start..."
    gcloud compute instances start "$INSTANCE" \
      --zone="$ZONE" --project="$PROJECT" >/dev/null
    ok "start requested (elapsed $(elapsed))."
    ;;
  STOPPING)
    warn "VM is STOPPING — waiting for TERMINATED before restart..."
    for i in $(seq 1 30); do
      S=$(gcloud compute instances describe "$INSTANCE" --zone="$ZONE" \
        --project="$PROJECT" --format="value(status)")
      [ "$S" = "TERMINATED" ] && break
      sleep 5
    done
    gcloud compute instances start "$INSTANCE" \
      --zone="$ZONE" --project="$PROJECT" >/dev/null
    ok "start requested after wait (elapsed $(elapsed))."
    ;;
  NOT_FOUND)
    err "instance $INSTANCE not found in $ZONE ($PROJECT)."
    exit 1
    ;;
  *)
    err "unexpected status: $STATUS"
    exit 1
    ;;
esac

IP=$(gcloud compute instances describe "$INSTANCE" --zone="$ZONE" --project="$PROJECT" \
  --format="value(networkInterfaces[0].accessConfigs[0].natIP)" 2>/dev/null || echo "")
MACHINE=$(gcloud compute instances describe "$INSTANCE" --zone="$ZONE" --project="$PROJECT" \
  --format="value(machineType.basename())" 2>/dev/null || echo "")
log "  machine: ${BOLD}${MACHINE}${RST}   external IP: ${BOLD}${IP:-n/a}${RST}"

# --- 2. SSH reachable ------------------------------------------------------
step "2/5  Waiting for SSH (boot + startup script)"
for i in $(seq 1 30); do
  if gcloud compute ssh "$INSTANCE" --zone="$ZONE" --project="$PROJECT" \
       --command="true" >/dev/null 2>&1; then
    ok "SSH ready (attempt $i, elapsed $(elapsed))."
    break
  fi
  printf "  %s…%s waiting for SSH (attempt %d/30, elapsed %s)\r" "$DIM" "$RST" "$i" "$(elapsed)"
  sleep 10
  if [ "$i" -eq 30 ]; then
    echo; err "SSH never came up after 5min."; exit 1
  fi
done
echo

# --- 3. Containers healthy -------------------------------------------------
step "3/5  Waiting for Docker containers (aneel-qa + aneel-qdrant)"
for i in $(seq 1 60); do
  OUT=$(gcloud compute ssh "$INSTANCE" --zone="$ZONE" --project="$PROJECT" \
    --command="sudo docker ps --format '{{.Names}}|{{.Status}}'" 2>/dev/null || true)
  QA_STATUS=$(echo "$OUT" | awk -F'|' '$1=="aneel-qa"{print $2}')
  QD_STATUS=$(echo "$OUT" | awk -F'|' '$1=="aneel-qdrant"{print $2}')
  printf "  %s…%s qa=[%s] qdrant=[%s] (attempt %d/60, elapsed %s)    \r" \
    "$DIM" "$RST" "${QA_STATUS:-missing}" "${QD_STATUS:-missing}" "$i" "$(elapsed)"
  if echo "${QA_STATUS:-}" | grep -q "healthy" && [ -n "${QD_STATUS:-}" ]; then
    echo
    ok "aneel-qa healthy, aneel-qdrant up (elapsed $(elapsed))."
    break
  fi
  sleep 10
  if [ "$i" -eq 60 ]; then
    echo
    err "containers did not become healthy in 10min."
    echo "  debug: sudo docker logs --tail 100 aneel-qa"
    exit 1
  fi
done

# --- 4. Internal health probe ---------------------------------------------
step "4/5  Internal /health probe"
HEALTH=$(gcloud compute ssh "$INSTANCE" --zone="$ZONE" --project="$PROJECT" \
  --command="curl -sf -m 5 http://localhost:8080/health" 2>/dev/null || echo "")
if [ "$HEALTH" = '{"status":"ok"}' ]; then
  ok "/health = ${HEALTH}"
else
  err "/health unexpected: ${HEALTH:-<empty>}"
  exit 1
fi

# --- 5. Warm-up (BGE-M3 + reranker + doc_index) ---------------------------
step "5/5  Warming up models (first /ask triggers model loads — ~60-180s)"
log "  sending warm-up query, logs streamed from VM..."
WARM_START=$(date +%s)
WARM_OUT=$(gcloud compute ssh "$INSTANCE" --zone="$ZONE" --project="$PROJECT" \
  --command="curl -s -m 600 -X POST http://localhost:8080/ask \
    -H 'Content-Type: application/json' \
    -d '$WARMUP_QUERY'" 2>/dev/null || echo "")
WARM_SECS=$(( $(date +%s) - WARM_START ))

if echo "$WARM_OUT" | grep -q '"answer"'; then
  ok "models warm (warm-up took ${WARM_SECS}s)."
else
  warn "warm-up response did not return an answer field."
  log "  raw output (first 300 chars): ${WARM_OUT:0:300}"
  log "  /ask may still be loading — try again in a minute."
fi

# --- summary ---------------------------------------------------------------
echo
printf "%s%s✔ aneel-rag-vm ready (total elapsed %s)%s\n" "$BOLD" "$GRN" "$(elapsed)" "$RST"
cat <<EOF

${BOLD}Next step${RST}: open IAP tunnel in another terminal:

  gcloud compute start-iap-tunnel $INSTANCE 8080 \\
    --local-host-port=localhost:8081 --zone=$ZONE --project=$PROJECT

Then query:

  curl -X POST http://localhost:8081/ask -H 'Content-Type: application/json' \\
    -d '{"question":"..."}'

${DIM}Stop the VM when done: bash deploy/stop.sh${RST}
EOF
