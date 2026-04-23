#!/usr/bin/env bash
# Stop aneel-rag-vm to halt compute charges. Disks persist (SSD ~$8/mo).
# Usage: bash deploy/stop.sh
set -euo pipefail

INSTANCE="aneel-rag-vm"
ZONE="us-central1-a"
PROJECT="desafio-rag"

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

# --- 1. Check state --------------------------------------------------------
step "1/2  Checking VM state  (${CYN}${INSTANCE}${RST} @ ${ZONE})"
STATUS=$(gcloud compute instances describe "$INSTANCE" \
  --zone="$ZONE" --project="$PROJECT" \
  --format="value(status)" 2>/dev/null || echo "NOT_FOUND")
log "  current status: ${BOLD}${STATUS}${RST}"

case "$STATUS" in
  TERMINATED|STOPPED|SUSPENDED)
    ok "VM already $STATUS — nothing to do. No compute charges."
    exit 0
    ;;
  STOPPING)
    warn "VM is already stopping — waiting for TERMINATED..."
    ;;
  RUNNING)
    log "  issuing stop..."
    gcloud compute instances stop "$INSTANCE" \
      --zone="$ZONE" --project="$PROJECT" >/dev/null
    ok "stop requested (elapsed $(elapsed))."
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

# --- 2. Wait for TERMINATED ------------------------------------------------
step "2/2  Waiting for TERMINATED"
for i in $(seq 1 30); do
  S=$(gcloud compute instances describe "$INSTANCE" \
    --zone="$ZONE" --project="$PROJECT" \
    --format="value(status)" 2>/dev/null)
  printf "  %s…%s status=[%s] (attempt %d/30, elapsed %s)    \r" \
    "$DIM" "$RST" "$S" "$i" "$(elapsed)"
  if [ "$S" = "TERMINATED" ]; then
    echo
    printf "\n%s%s✔ VM TERMINATED (total elapsed %s)%s\n" "$BOLD" "$GRN" "$(elapsed)" "$RST"
    log "  compute charges halted."
    log "  persistent disks still billed: 30GB boot + 50GB SSD ≈ \$8/mo."
    log "  restart with: ${BOLD}bash deploy/start.sh${RST}"
    exit 0
  fi
  sleep 10
done
echo
err "timeout waiting for TERMINATED (5min). Check: gcloud compute instances list"
exit 1
