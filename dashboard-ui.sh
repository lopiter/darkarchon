#!/usr/bin/env bash
# dashboard-ui.sh — start / stop / status the Vite dev server for the
# dashboard frontend. Companion to dashboard.sh (which runs the hub).
#
# Auto-syncs vite.config.ts proxy target to whatever hub port the local
# agent.config points at, so you don't have to remember to edit two files
# in sync after changing the hub port.
#
# Usage:
#   dashboard-ui.sh start       # 5173
#   dashboard-ui.sh stop
#   dashboard-ui.sh status
#   dashboard-ui.sh open        # open browser to http://localhost:5173
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/lib/_lib.sh"  # provides SESSION_NAME, STATE_DIR, AGENT_CONFIG

UI_DIR="$HERE/dashboard-ui"
VITE_PORT=5173
PIDFILE="${TMPDIR:-/tmp}/${SESSION_NAME}-dashboard-ui.pid"
LOGFILE="${TMPDIR:-/tmp}/${SESSION_NAME}-dashboard-ui.log"
VITE_CONFIG="$UI_DIR/vite.config.ts"

sync_vite_proxy_to_hub() {
    # Look up current hub port from agent.config (HUB_URL=http://127.0.0.1:PORT).
    # If we can't find one, leave vite.config.ts as-is — user gets a proxy
    # error in the browser if it's wrong, which is loud enough.
    [ -f "$AGENT_CONFIG" ] || return 0
    local hub_url
    hub_url="$(awk -F= '/^HUB_URL=/{print $2; exit}' "$AGENT_CONFIG" | tr -d '[:space:]')"
    [ -n "$hub_url" ] || return 0
    local hub_port
    hub_port="$(printf '%s' "$hub_url" | sed -E 's|.*:([0-9]+)/?.*|\1|')"
    [ -n "$hub_port" ] || return 0

    local want="target: 'http://localhost:$hub_port',"
    if grep -qF "$want" "$VITE_CONFIG"; then
        return 0
    fi
    sed -i.bak -E "s|target: 'http://localhost:[0-9]+'|target: 'http://localhost:$hub_port'|" "$VITE_CONFIG"
    rm -f "$VITE_CONFIG.bak"
    echo "synced vite proxy → http://localhost:$hub_port"
}

cmd="${1:-status}"

case "$cmd" in
    start)
        if [ ! -d "$UI_DIR/node_modules" ]; then
            echo "node_modules missing — running npm install (one-time)..."
            (cd "$UI_DIR" && npm install)
        fi

        sync_vite_proxy_to_hub

        if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
            echo "[$SESSION_NAME] dashboard-ui already running (pid $(cat "$PIDFILE")) → http://localhost:$VITE_PORT"
            exit 0
        fi

        # Clear stale listener (e.g. crashed vite) so strictPort doesn't bite.
        if lsof -ti ":$VITE_PORT" >/dev/null 2>&1; then
            echo "port $VITE_PORT busy — killing stale listener"
            lsof -ti ":$VITE_PORT" | xargs kill -9 2>/dev/null || true
            sleep 1
        fi

        (cd "$UI_DIR" && nohup npm run dev >"$LOGFILE" 2>&1 &
            echo $! > "$PIDFILE")
        sleep 2
        if kill -0 "$(cat "$PIDFILE")" 2>/dev/null && lsof -ti ":$VITE_PORT" >/dev/null 2>&1; then
            echo "started dashboard-ui [$SESSION_NAME] (pid $(cat "$PIDFILE")) → http://localhost:$VITE_PORT"
            echo "log: $LOGFILE"
        else
            echo "dashboard-ui failed to start. log:"
            cat "$LOGFILE"
            exit 1
        fi
        ;;
    stop)
        if [ ! -f "$PIDFILE" ]; then
            echo "[$SESSION_NAME] dashboard-ui not running (no pidfile)"
            # Best-effort: free port even if pidfile missing.
            if lsof -ti ":$VITE_PORT" >/dev/null 2>&1; then
                echo "found stray listener on $VITE_PORT — killing"
                lsof -ti ":$VITE_PORT" | xargs kill -9 2>/dev/null || true
            fi
            exit 0
        fi
        pid="$(cat "$PIDFILE")"
        if kill -0 "$pid" 2>/dev/null; then
            # npm spawns vite as a child; kill the process group to catch both.
            pkill -P "$pid" 2>/dev/null || true
            kill "$pid" 2>/dev/null || true
            sleep 1
            kill -9 "$pid" 2>/dev/null || true
            echo "stopped dashboard-ui [$SESSION_NAME] (pid $pid)"
        else
            echo "[$SESSION_NAME] stale pidfile (pid $pid not alive)"
        fi
        rm -f "$PIDFILE"
        # Mop up any vite child that survived the parent npm wrapper.
        if lsof -ti ":$VITE_PORT" >/dev/null 2>&1; then
            lsof -ti ":$VITE_PORT" | xargs kill -9 2>/dev/null || true
        fi
        ;;
    status)
        echo "session:    $SESSION_NAME"
        echo "ui-dir:     $UI_DIR"
        echo "vite port:  $VITE_PORT"
        if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
            echo "status:     running (pid $(cat "$PIDFILE"))"
            echo "log:        $LOGFILE"
        else
            echo "status:     not running"
        fi
        ;;
    open)
        open "http://localhost:$VITE_PORT" 2>/dev/null || echo "open http://localhost:$VITE_PORT"
        ;;
    *)
        echo "Usage: $0 {start|stop|status|open}" >&2
        exit 1
        ;;
esac
