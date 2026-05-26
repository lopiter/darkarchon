#!/usr/bin/env bash
# agent.sh — start / stop / status the per-host agent.
#
# Reads config from $STATE_DIR/agent.config by default.
# Multi-team aware: derives pidfile from SESSION_NAME so concurrent
# worktree agents don't collide.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/lib/_lib.sh"  # provides SESSION_NAME, STATE_DIR
# Make STATE_DIR available to agent.py — it falls back to /dev/null otherwise,
# which means the registry isn't picked up and workers show as 'discovered'.
export STATE_DIR

PIDFILE="${TMPDIR:-/tmp}/${SESSION_NAME}-agent.pid"
LOGFILE="${TMPDIR:-/tmp}/${SESSION_NAME}-agent.log"
CONFIG="${STATE_DIR}/agent.config"

# Interactive config setup. Asks the user for missing required values when
# stdin is a TTY. Skipped (with error) for non-interactive invocations like
# nohup/systemd; user is expected to provide $CONFIG ahead of time then.
_init_config_interactive() {
    if [ ! -t 0 ]; then
        echo "ERROR: $CONFIG not found and stdin is not a TTY." >&2
        echo "Copy $HERE/agent.config.example to $CONFIG and edit before retrying," >&2
        echo "or run this command interactively to be prompted." >&2
        exit 1
    fi
    mkdir -p "$(dirname "$CONFIG")"
    echo "agent.config not found at $CONFIG — let's set it up."
    echo ""
    local hub_url host_id default_host
    while true; do
        read -r -p "Hub URL (e.g. http://192.168.0.10:8774): " hub_url
        case "$hub_url" in
            http://*|https://*) break ;;
            *) echo "  must start with http:// or https://" ;;
        esac
    done
    default_host="$(hostname -s 2>/dev/null || hostname)"
    read -r -p "Host ID [$default_host]: " host_id
    [ -z "$host_id" ] && host_id="$default_host"
    cat > "$CONFIG" <<EOF
HUB_URL=$hub_url
HOST_ID=$host_id
INTERVAL=5
# Include 'node' because Claude Code on macOS often reports its pane process
# as the node runtime (or its version string).
LLM_PROCESSES=claude,node
# Mark panes with \`tmux rename-window claude\` to flag them as Claude workers
# explicitly (bypasses process-name auto-detection).
LLM_WINDOWS=claude
EOF
    echo "Wrote $CONFIG"
    echo ""
}

# Prompt only for HUB_URL when config exists but the value is empty/missing.
_prompt_hub_url_if_missing() {
    local current
    current="$(grep -E '^HUB_URL=' "$CONFIG" 2>/dev/null | head -1 | cut -d= -f2- || true)"
    if [ -n "$current" ]; then
        return 0
    fi
    if [ ! -t 0 ]; then
        echo "ERROR: $CONFIG has no HUB_URL and stdin is not a TTY." >&2
        exit 1
    fi
    local hub_url
    while true; do
        read -r -p "HUB_URL missing in $CONFIG. Enter hub URL (e.g. http://192.168.0.10:8774): " hub_url
        case "$hub_url" in
            http://*|https://*) break ;;
            *) echo "  must start with http:// or https://" ;;
        esac
    done
    # Replace or append HUB_URL line
    if grep -qE '^HUB_URL=' "$CONFIG"; then
        # BSD sed compatible in-place edit
        sed -i.bak -E "s|^HUB_URL=.*|HUB_URL=$hub_url|" "$CONFIG" && rm -f "${CONFIG}.bak"
    else
        printf '\nHUB_URL=%s\n' "$hub_url" >> "$CONFIG"
    fi
    echo "Updated $CONFIG"
}

cmd="${1:-status}"

case "$cmd" in
    start)
        if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
            echo "[$SESSION_NAME] agent already running (pid $(cat "$PIDFILE"))"
            exit 0
        fi
        if [ ! -f "$CONFIG" ]; then
            _init_config_interactive
        else
            _prompt_hub_url_if_missing
        fi
        nohup python3 "$HERE/agent.py" --config "$CONFIG" >"$LOGFILE" 2>&1 &
        echo $! > "$PIDFILE"
        sleep 0.5
        if kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
            echo "started agent [$SESSION_NAME] (pid $(cat "$PIDFILE"))"
            echo "log: $LOGFILE"
        else
            echo "failed to start. log:"
            cat "$LOGFILE"
            exit 1
        fi
        ;;
    stop)
        if [ ! -f "$PIDFILE" ]; then
            echo "[$SESSION_NAME] agent not running (no pidfile)"
            exit 0
        fi
        pid="$(cat "$PIDFILE")"
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid"
            echo "stopped agent [$SESSION_NAME] (pid $pid)"
        else
            echo "[$SESSION_NAME] stale pidfile (pid $pid not alive)"
        fi
        rm -f "$PIDFILE"
        ;;
    status)
        echo "session:    $SESSION_NAME"
        echo "config:     $CONFIG"
        if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
            echo "status:     running (pid $(cat "$PIDFILE"))"
            echo "log:        $LOGFILE"
        else
            echo "status:     not running"
        fi
        ;;
    *)
        echo "Usage: $0 {start|stop|status}" >&2
        exit 1
        ;;
esac
