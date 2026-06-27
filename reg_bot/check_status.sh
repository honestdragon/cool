#!/usr/bin/env bash
set -euo pipefail

REG_BOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$REG_BOT_DIR/_common.sh"
load_config

echo "=== reg_bot status ==="
echo "script:  $REG_BOT_SCRIPT"
echo "python:  $PYTHON"
echo "stdout:  $STDOUT_LOG"
echo "results: $RESULTS_LOG"
echo

running=0

if systemctl list-unit-files "$SERVICE_NAME" >/dev/null 2>&1; then
    systemd_state="$(systemctl is-active "$SERVICE_NAME" 2>/dev/null || true)"
    systemd_enabled="$(systemctl is-enabled "$SERVICE_NAME" 2>/dev/null || true)"
    echo "systemd: $SERVICE_NAME"
    echo "  active:  $systemd_state"
    echo "  enabled: $systemd_enabled"
    if [[ "$systemd_state" == "active" ]]; then
        running=1
        main_pid="$(systemctl show -p MainPID --value "$SERVICE_NAME" 2>/dev/null || true)"
        [[ -n "$main_pid" && "$main_pid" != "0" ]] && echo "  pid:     $main_pid"
    fi
    echo
fi

pid="$(pid_from_file)"
if [[ -n "$pid" ]]; then
    if process_alive "$pid"; then
        running=1
        echo "nohup daemon: running (pid $pid)"
        if command -v ps >/dev/null 2>&1; then
            ps -p "$pid" -o pid=,etime=,cmd= 2>/dev/null | sed 's/^/  /'
        fi
    else
        echo "nohup daemon: not running (stale pid file: $pid)"
    fi
    echo
fi

pgrep_output="$(reg_bot_pgrep)"
if [[ -n "$pgrep_output" ]]; then
    running=1
    echo "matching processes:"
    echo "$pgrep_output" | sed 's/^/  /'
    echo
elif [[ "$running" -eq 0 ]]; then
    echo "matching processes: none"
    echo
fi

if [[ "$running" -eq 1 ]]; then
    echo "overall: RUNNING"
else
    echo "overall: NOT RUNNING"
fi

if [[ -f "$STDOUT_LOG" ]]; then
    echo
    echo "=== last 15 stdout lines ==="
    tail -n 15 "$STDOUT_LOG"
fi

if [[ -f "$RESULTS_LOG" ]]; then
    echo
    echo "=== last 3 result entries ==="
    tail -n 3 "$RESULTS_LOG"
fi
