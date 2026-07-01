#!/usr/bin/env bash
# Scheduled ORO agent submit bot for ag_62.py
set -euo pipefail

ORO_DIR="/root/work/oro"
AGENT_FILE="ag_62.py"
AGENT_NAME="achieve"
WALLET_NAME="honestdragon"
WALLET_HOTKEY="ht-test"
WAIT_SECONDS=$((7 * 3600 + 15 * 60))  # 7h 15m

LOG_DIR="${ORO_DIR}/logs"
LOG_FILE="${LOG_DIR}/submit_ag62_bot.log"
PID_FILE="${LOG_DIR}/submit_ag62_bot.pid"

mkdir -p "${LOG_DIR}"

log() {
  echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] $*" | tee -a "${LOG_FILE}"
}

submit_agent() {
  cd "${ORO_DIR}"
  oro submit \
    --agent-name "${AGENT_NAME}" \
    --agent-file "${AGENT_FILE}" \
    --wallet-name "${WALLET_NAME}" \
    --wallet-hotkey "${WALLET_HOTKEY}"
}

log "Submit bot started (pid $$)"
log "Agent: ${AGENT_NAME} (${AGENT_FILE})"
log "Wallet: ${WALLET_NAME}/${WALLET_HOTKEY}"
log "Waiting ${WAIT_SECONDS}s (7h 15m) before submit"
log "Target submit time: $(date -u -d "+${WAIT_SECONDS} seconds" '+%Y-%m-%d %H:%M:%S UTC')"

sleep "${WAIT_SECONDS}"

log "Wait complete — submitting agent"

attempt=1
max_attempts=48
while true; do
  log "Submit attempt ${attempt}/${max_attempts}"
  set +e
  output="$(submit_agent 2>&1)"
  status=$?
  set -e
  printf '%s\n' "${output}" | tee -a "${LOG_FILE}"

  if [[ ${status} -eq 0 ]]; then
    log "Submit succeeded"
    exit 0
  fi

  if [[ "${output}" =~ [Pp]lease\ wait\ ([0-9]+)\ seconds ]]; then
    cooldown="${BASH_REMATCH[1]}"
    if (( attempt >= max_attempts )); then
      log "Cooldown still active (${cooldown}s remaining) — giving up"
      exit 1
    fi
    # Wait a bit past the reported cooldown, capped at 15 minutes per retry.
    wait_for=$((cooldown + 30))
    if (( wait_for > 900 )); then
      wait_for=900
    fi
    log "Cooldown active — waiting ${wait_for}s before retry"
    sleep "${wait_for}"
    attempt=$((attempt + 1))
    continue
  fi

  log "Submit failed (exit ${status})"
  exit "${status}"
done
