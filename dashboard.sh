#!/usr/bin/env bash
# dashboard.sh — start / stop / status the read-only dashboard.
#
# Multi-team aware: derives port + pidfile + logfile from current SESSION_NAME
# (set by lib/_lib.sh after sourcing config.env). Each worktree's team gets
# its own port and state slot — running multiple dashboards in parallel is
# safe and conflict-free.
#
# Usage:
#   dashboard.sh start [port]   default: 8765 + hash(SESSION_NAME) % 100
#   dashboard.sh stop
#   dashboard.sh status
#   dashboard.sh open           open browser to dashboard
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/lib/_lib.sh"  # provides SESSION_NAME, STATE_DIR

# Deterministic port from session name hash (stable across restarts)
PORT_BASE=8765
PORT_OFFSET=$(printf '%s' "$SESSION_NAME" | cksum | awk '{print $1 % 100}')
DEFAULT_PORT=$((PORT_BASE + PORT_OFFSET))

PIDFILE="${TMPDIR:-/tmp}/${SESSION_NAME}-dashboard.pid"
LOGFILE="${TMPDIR:-/tmp}/${SESSION_NAME}-dashboard.log"

cmd="${1:-status}"

case "$cmd" in
    start)
        port="${2:-$DEFAULT_PORT}"
        # ── Hub ────────────────────────────────────────────────────
        if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
            echo "[$SESSION_NAME] hub already running (pid $(cat "$PIDFILE")) → http://localhost:$port"
        else
            nohup python3 "$HERE/dashboard.py" \
                --port "$port" \
                --host 0.0.0.0 \
                --session-name "$SESSION_NAME" \
                --state-dir "$STATE_DIR" \
                >"$LOGFILE" 2>&1 &
            echo $! > "$PIDFILE"
            sleep 0.5
            if kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
                echo "started hub [$SESSION_NAME] (pid $(cat "$PIDFILE")) → http://localhost:$port"
                echo "log: $LOGFILE"
            else
                echo "hub failed to start. log:"
                cat "$LOGFILE"
                exit 1
            fi
        fi

        # ── Local agent (auto-create or sync config) ───────────────
        AGENT_CONFIG="${STATE_DIR}/agent.config"
        if [ ! -f "$AGENT_CONFIG" ]; then
            mkdir -p "$(dirname "$AGENT_CONFIG")"
            cat > "$AGENT_CONFIG" <<EOF
HUB_URL=http://127.0.0.1:$port
HOST_ID=$(hostname -s 2>/dev/null || hostname)
INTERVAL=5
# Include 'node' because Claude Code on macOS often reports its pane process
# as the node runtime (or its version string). Scanner verifies with prompt
# marker before classifying, so non-Claude node panes are safe.
LLM_PROCESSES=claude,node
# Mark panes with \`tmux rename-window claude\` to flag them as Claude workers
# explicitly (bypasses process-name auto-detection).
LLM_WINDOWS=claude
EOF
            echo "created default agent config at $AGENT_CONFIG"
        else
            # Sync HUB_URL to whatever port the hub is starting on this run.
            # Other fields (HOST_ID, LLM_*) are preserved — the user may have
            # customized them. Skip the rewrite when nothing would change so
            # we don't churn the file's mtime on every start.
            current="$(grep '^HUB_URL=' "$AGENT_CONFIG" 2>/dev/null || true)"
            want="HUB_URL=http://127.0.0.1:$port"
            if [ "$current" != "$want" ]; then
                # macOS-portable in-place sed (gives us "$f.bak" we remove).
                sed -i.bak "s|^HUB_URL=.*|$want|" "$AGENT_CONFIG"
                rm -f "$AGENT_CONFIG.bak"
                echo "synced HUB_URL in $AGENT_CONFIG → http://127.0.0.1:$port"
            fi
        fi
        "$HERE/agent.sh" start || true

        # ── Friendly LAN IPs ───────────────────────────────────────
        echo ""
        echo "Reachable at:"
        echo "  - http://localhost:$port"
        echo "  - http://127.0.0.1:$port"
        # External IPs for LAN / shared-network access
        ifconfig 2>/dev/null \
            | awk '/^[a-z]/{iface=$1} /inet /{if ($2 != "127.0.0.1") print iface, $2}' \
            | sed 's/://' \
            | while read -r iface ip; do
                echo "  - http://$ip:$port  ($iface)"
            done
        ;;
    stop)
        "$HERE/agent.sh" stop || true
        if [ ! -f "$PIDFILE" ]; then
            echo "[$SESSION_NAME] hub not running (no pidfile)"
            exit 0
        fi
        pid="$(cat "$PIDFILE")"
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid"
            echo "stopped hub [$SESSION_NAME] (pid $pid)"
        else
            echo "[$SESSION_NAME] stale pidfile (pid $pid not alive)"
        fi
        rm -f "$PIDFILE"
        ;;
    status)
        echo "session:    $SESSION_NAME"
        echo "state-dir:  $STATE_DIR"
        echo "default port: $DEFAULT_PORT"
        if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
            echo "status:     running (pid $(cat "$PIDFILE"))"
            echo "log:        $LOGFILE"
        else
            echo "status:     not running"
        fi
        ;;
    open)
        port="${2:-$DEFAULT_PORT}"
        open "http://localhost:$port" 2>/dev/null || echo "open http://localhost:$port"
        ;;
    *)
        echo "Usage: $0 {start [port]|stop|status|open [port]}" >&2
        exit 1
        ;;
esac
