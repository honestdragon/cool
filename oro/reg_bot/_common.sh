#!/usr/bin/env bash
# Shared paths and helpers for reg_bot daemon scripts.

REG_BOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ORO_ROOT="$(cd "$REG_BOT_DIR/.." && pwd)"
REG_BOT_SCRIPT="$REG_BOT_DIR/reg_bot.py"
PID_FILE="$REG_BOT_DIR/reg_bot.pid"
STDOUT_LOG="$REG_BOT_DIR/reg_bot.stdout.log"
CONFIG_FILE="$REG_BOT_DIR/config.env"
RESULTS_LOG="$REG_BOT_DIR/logs/reg_bot_results.jsonl"
SERVICE_NAME="reg-bot.service"
SYSTEMD_UNIT="$REG_BOT_DIR/$SERVICE_NAME"

PYTHON="${REG_BOT_PYTHON:-/root/.venv/bin/python3}"
REG_BOT_ARGS="${REG_BOT_ARGS:---verbose}"

load_config() {
    if [[ -f "$CONFIG_FILE" ]]; then
        set -a
        # shellcheck disable=SC1090
        source "$CONFIG_FILE"
        set +a
    fi

    if [[ ! -x "$PYTHON" ]]; then
        PYTHON="$(command -v python3)"
    fi
}

pid_from_file() {
    if [[ -f "$PID_FILE" ]]; then
        tr -d '[:space:]' <"$PID_FILE"
    fi
}

process_alive() {
    local pid="$1"
    [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

reg_bot_pgrep() {
    pgrep -af "$REG_BOT_SCRIPT" 2>/dev/null || true
}

systemd_available() {
    command -v systemctl >/dev/null 2>&1 \
        && systemctl list-unit-files "$SERVICE_NAME" >/dev/null 2>&1 \
        && systemctl is-enabled "$SERVICE_NAME" >/dev/null 2>&1
}

using_systemd() {
    systemd_available && systemctl is-active --quiet "$SERVICE_NAME"
}
