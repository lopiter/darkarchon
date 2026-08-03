#!/usr/bin/env bash
# agent.sh — start / stop / status the per-host agent.
#
# Reads config from $HOST_STATE_DIR/agent.config.
#
# One agent per host, NOT per team: it scans every tmux pane on the machine
# (`tmux list-panes -a`) and POSTs them under a single HOST_ID. A second
# instance would report the same panes to the same endpoint, so the pidfile is
# host-level and start refuses when any agent process is already alive.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/lib/_lib.sh"  # provides HOST_STATE_DIR, AGENT_CONFIG, STATE_DIR
# Tell agent.py which root to sweep for team state dirs, so a non-default
# TOOL_PREFIX doesn't leave it scanning ~/.darkarchon.
export DARKARCHON_STATE_ROOT="$HOST_STATE_DIR"

PIDFILE="${TMPDIR:-/tmp}/${TOOL_PREFIX}-agent.pid"
LOGFILE="${TMPDIR:-/tmp}/${TOOL_PREFIX}-agent.log"
CONFIG="$AGENT_CONFIG"

# Every live agent.py owned by this user, one pid per line. Catches instances
# the pidfile lost track of — TMPDIR is periodically swept on macOS, and older
# versions keyed the pidfile by team so each team's start created another one.
_agent_pids() {
    pgrep -u "$(id -u)" -f "$HERE/agent.py" 2>/dev/null || true
}

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
        running="$(_agent_pids)"
        if [ -n "$running" ]; then
            echo "agent already running on this host (pid $(echo "$running" | tr '\n' ' '))"
            # Re-adopt so a later stop can reach it even if the pidfile was lost.
            echo "$running" | head -1 > "$PIDFILE"
            exit 0
        fi
        migrate_agent_config
        if [ ! -f "$CONFIG" ]; then
            _init_config_interactive
        else
            _prompt_hub_url_if_missing
        fi
        nohup python3 "$HERE/agent.py" --config "$CONFIG" >"$LOGFILE" 2>&1 &
        echo $! > "$PIDFILE"
        sleep 0.5
        if kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
            echo "started agent (pid $(cat "$PIDFILE"))"
            echo "log: $LOGFILE"
        else
            echo "failed to start. log:"
            cat "$LOGFILE"
            exit 1
        fi
        ;;
    stop)
        # Stop every agent process, not just the pidfile's — untracked strays
        # keep posting to the hub and are the whole reason we sweep by pgrep.
        pids="$(_agent_pids)"
        if [ -z "$pids" ]; then
            echo "agent not running"
            rm -f "$PIDFILE"
            exit 0
        fi
        for pid in $pids; do
            if kill "$pid" 2>/dev/null; then
                echo "stopped agent (pid $pid)"
            else
                echo "could not signal pid $pid" >&2
            fi
        done
        rm -f "$PIDFILE"
        ;;
    status)
        echo "state root: $HOST_STATE_DIR"
        echo "config:     $CONFIG"
        pids="$(_agent_pids)"
        if [ -n "$pids" ]; then
            echo "status:     running (pid $(echo "$pids" | tr '\n' ' '))"
            echo "log:        $LOGFILE"
            if [ "$(echo "$pids" | wc -l | tr -d ' ')" -gt 1 ]; then
                echo "WARNING:    more than one agent is running on this host —" >&2
                echo "            they overwrite each other's reports. Run '$0 stop' then start." >&2
            fi
        else
            echo "status:     not running"
        fi
        ;;
    *)
        echo "Usage: $0 {start|stop|status}" >&2
        exit 1
        ;;
esac
