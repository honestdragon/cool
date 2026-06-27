#!/usr/bin/env bash
set -euo pipefail

REG_BOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$REG_BOT_DIR/_common.sh"
load_config

stop_pid() {
    local pid="$1"
    local label="$2"

    if ! process_alive "$pid"; then
        echo "$label is not running (stale pid $pid)."
        return 0
    fi

    echo "Stopping $label (pid $pid)..."
    kill -TERM "$pid"

    for _ in $(seq 1 30); do
        if ! process_alive "$pid"; then
            echo "$label stopped."
            return 0
        fi
        sleep 1
    done

    echo "warning: $label did not exit after 30s, sending SIGKILL..."
    kill -KILL "$pid" 2>/dev/null || true
    sleep 1

    if process_alive "$pid"; then
        echo "error: failed to stop pid $pid" >&2
        return 1
    fi

    echo "$label stopped."
}

stopped=0

if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
    echo "Stopping systemd service $SERVICE_NAME..."
    systemctl stop "$SERVICE_NAME"
    stopped=1
fi

pid="$(pid_from_file)"
if [[ -n "$pid" ]]; then
    stop_pid "$pid" "reg_bot"
    rm -f "$PID_FILE"
    stopped=1
fi

# Fallback: stop any stray reg_bot.py processes not tracked by pid file.
while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    stray_pid="${line%% *}"
    if process_alive "$stray_pid"; then
        stop_pid "$stray_pid" "reg_bot (stray)"
        stopped=1
    fi
done < <(pgrep -f "$REG_BOT_SCRIPT" 2>/dev/null || true)

if [[ "$stopped" -eq 0 ]]; then
    echo "reg_bot is not running."
else
    echo "Done."
fi
