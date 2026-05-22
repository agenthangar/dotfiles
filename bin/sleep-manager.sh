#!/usr/bin/env bash
# sleep-manager.sh — manage macOS sleep behavior
# Usage: sleep-manager.sh {status|disable|enable}

set -euo pipefail

CAFFEINATE_PIDFILE="/tmp/sleep-manager-caffeinate.pid"

status() {
    echo "=== Current Sleep Settings ==="
    pmset -g | grep -E '^\s*(sleep|displaysleep|disksleep)\b' || true

    echo
    echo "=== Active Sleep Assertions ==="
    # Assertions are processes actively blocking sleep right now
    pmset -g assertions | grep -E 'PreventUserIdleSystemSleep|PreventUserIdleDisplaySleep' | grep -v '0$' || echo "(none)"

    echo
    if [[ -f "$CAFFEINATE_PIDFILE" ]] && kill -0 "$(cat "$CAFFEINATE_PIDFILE")" 2>/dev/null; then
        echo "=== Script-managed caffeinate: RUNNING (pid $(cat "$CAFFEINATE_PIDFILE")) ==="
    else
        echo "=== Script-managed caffeinate: not running ==="
    fi
}

enable_sleep() {
    # Restore default sleep behavior.
    # Kill our caffeinate process if running.
    if [[ -f "$CAFFEINATE_PIDFILE" ]]; then
        if kill -0 "$(cat "$CAFFEINATE_PIDFILE")" 2>/dev/null; then
            kill "$(cat "$CAFFEINATE_PIDFILE")"
            echo "Stopped caffeinate process."
        fi
        rm -f "$CAFFEINATE_PIDFILE"
    fi

    # Restore pmset defaults and clear the hard sleep lock.
    echo "Restoring pmset defaults (requires sudo)..."
    sudo pmset -a disablesleep 0 sleep 10 displaysleep 10 disksleep 10
    echo "Sleep re-enabled."
}

disable_sleep() {
    # Belt and suspenders, matching the user's ~/.zshrc nosleep style:
    #   1. pmset disablesleep 1 — the strongest macOS setting (survives logout)
    #   2. caffeinate -dimsu     — process assertion as a second layer
    # Unlike the interactive zshrc version, this runs caffeinate in the
    # background and records its PID so `enable` can stop it later.
    if [[ -f "$CAFFEINATE_PIDFILE" ]] && kill -0 "$(cat "$CAFFEINATE_PIDFILE")" 2>/dev/null; then
        echo "Sleep is already disabled (caffeinate pid $(cat "$CAFFEINATE_PIDFILE"))."
        exit 0
    fi

    echo "Disabling sleep (requires sudo)..."
    sudo pmset -a disablesleep 1
    nohup caffeinate -dimsu >/dev/null 2>&1 &
    echo $! > "$CAFFEINATE_PIDFILE"
    disown 2>/dev/null || true
    echo "Sleep disabled. caffeinate pid: $(cat "$CAFFEINATE_PIDFILE")"
}

case "${1:-}" in
    status)  status ;;
    disable) disable_sleep ;;
    enable)  enable_sleep ;;
    *)
        echo "Usage: $0 {status|disable|enable}" >&2
        exit 1
        ;;
esac
