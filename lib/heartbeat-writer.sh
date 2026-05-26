#!/usr/bin/env bash
# Worker-side heartbeat writer.
#
# Spawned in the background by start-worker-claude.sh. Writes a small JSON
# file every $HEARTBEAT_INTERVAL seconds while the watched pid stays alive
# (== the wrapper's pid, which after `exec claude` is the claude process).
# Exits and removes its heartbeat file when the watched pid goes away.
#
# Usage:
#   heartbeat-writer.sh <worker_name> <state_dir> <watch_pid>
#
# Heartbeat file:
#   $STATE_DIR/heartbeats/<safe_worker_name>.json
#   {
#     "worker": "...",
#     "pid": 12345,
#     "last_seen": "2026-05-25T12:00:00Z",
#     "last_seen_epoch": 1779638400
#   }
#
# Consumed by agent.py to mark workers dead when the heartbeat goes stale —
# more reliable than tmux pane scan (catches crashed claude processes whose
# tmux pane still exists with a dead shell).
set -euo pipefail

if [ $# -lt 3 ]; then
    echo "Usage: $0 <worker_name> <state_dir> <watch_pid>" >&2
    exit 1
fi

WORKER_NAME="$1"
STATE_DIR="$2"
WATCH_PID="$3"
INTERVAL="${HEARTBEAT_INTERVAL:-5}"

HB_DIR="$STATE_DIR/heartbeats"
mkdir -p "$HB_DIR"

# Sanitize for filename — must match agent.py's worker→file mapping.
SAFE="$(printf '%s' "$WORKER_NAME" | tr -c '[:alnum:]_' '_')"
HB_FILE="$HB_DIR/${SAFE}.json"

cleanup() {
    rm -f "$HB_FILE" 2>/dev/null || true
}
trap cleanup EXIT INT TERM HUP

while kill -0 "$WATCH_PID" 2>/dev/null; do
    now_iso="$(date -u +%FT%TZ)"
    now_epoch="$(date +%s)"
    tmp="$HB_FILE.tmp.$$"
    cat > "$tmp" <<EOF
{
  "worker": "$WORKER_NAME",
  "pid": $WATCH_PID,
  "last_seen": "$now_iso",
  "last_seen_epoch": $now_epoch
}
EOF
    mv "$tmp" "$HB_FILE"
    sleep "$INTERVAL"
done
