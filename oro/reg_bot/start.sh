#!/usr/bin/env bash
set -euo pipefail

REG_BOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$REG_BOT_DIR/_common.sh"
load_config

if [[ ! -f "$REG_BOT_SCRIPT" ]]; then
    echo "error: reg_bot.py not found at $REG_BOT_SCRIPT" >&2
    exit 1
fi

if [[ ! -x "$PYTHON" ]]; then
    echo "error: python not found or not executable: $PYTHON" >&2
    exit 1
fi

if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
    echo "reg_bot is already running via systemd ($SERVICE_NAME)."
    exit 0
fi

existing_pid="$(pid_from_file)"
if process_alive "$existing_pid"; then
    echo "reg_bot is already running (pid $existing_pid)."
    exit 0
fi

if [[ -f "$PID_FILE" ]]; then
    rm -f "$PID_FILE"
fi

mkdir -p "$(dirname "$STDOUT_LOG")" "$(dirname "$RESULTS_LOG")"

# shellcheck disable=SC2206
extra_args=( $REG_BOT_ARGS )

echo "Starting reg_bot..."
echo "  python:  $PYTHON"
echo "  script:  $REG_BOT_SCRIPT"
echo "  args:    ${extra_args[*]:-<none>}"
echo "  log:     $STDOUT_LOG"

nohup env \
    ${REG_BOT_WALLET_PASSWORD:+REG_BOT_WALLET_PASSWORD="$REG_BOT_WALLET_PASSWORD"} \
    ${REG_BOT_WEBHOOK_URL:+REG_BOT_WEBHOOK_URL="$REG_BOT_WEBHOOK_URL"} \
    "$PYTHON" "$REG_BOT_SCRIPT" "${extra_args[@]}" \
    >>"$STDOUT_LOG" 2>&1 &

new_pid=$!
echo "$new_pid" >"$PID_FILE"

sleep 1
if process_alive "$new_pid"; then
    echo "reg_bot started (pid $new_pid)."
    echo "Check status: $REG_BOT_DIR/check_status.sh"
    echo "Follow logs:  tail -f $STDOUT_LOG"
else
    echo "error: reg_bot exited immediately. Last log lines:" >&2
    tail -n 20 "$STDOUT_LOG" >&2 || true
    rm -f "$PID_FILE"
    exit 1
fi
