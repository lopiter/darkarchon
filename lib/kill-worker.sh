#!/usr/bin/env bash
# Kill a worker pane and remove it from the runtime registry.
#
# Usage:
#   kill-worker.sh <name>
#
# Behavior:
#   - Refuses to kill workers defined statically in config.env (use stop.sh for the whole session).
#   - Kills only the tmux window (not the session).
#   - Strips WORKER_<name>_* entries from $STATE_DIR/workers-runtime.env.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/_lib.sh"

if [ $# -lt 1 ]; then
    echo "Usage: $0 <name>" >&2
    exit 1
fi
NAME="$1"

# Refuse to kill statically-defined workers
for static in "${WORKERS[@]:-}"; do
    if [ "$static" = "$NAME" ]; then
        echo "ERROR: '$NAME' is a static worker in config.env. Use stop.sh to end the whole session." >&2
        exit 1
    fi
done

TARGET="$(worker_target "$NAME")"
if [ -z "$TARGET" ]; then
    echo "ERROR: no worker '$NAME' registered" >&2
    exit 1
fi

SAFE="$(safe_name "$NAME")"

# Kill the tmux window only if it's OURS: either in this team's session, or in
# a dedicated session we created for it at spawn time (--session, recorded as
# WORKER_<sn>_SESSION). Anything else is an invited external pane — refuse.
_SESSION_VAR="WORKER_${SAFE}_SESSION"
SPAWNED_SESSION="${!_SESSION_VAR:-}"
if [ "${TARGET%%:*}" != "$SESSION_NAME" ] && [ "${TARGET%%:*}" != "$SPAWNED_SESSION" ]; then
    echo "ERROR: refuse to kill external worker '$NAME' at '$TARGET'." >&2
    echo "Only manage panes inside session '$SESSION_NAME'." >&2
    exit 1
fi

tmux kill-window -t "=$TARGET" 2>/dev/null || true

# Strip from runtime registry under a lock — concurrent spawn appending while
# we rewrite would lose that spawn's entry.
RT="$STATE_DIR/workers-runtime.env"
_kill_and_strip() {
    # Leave a recall record so this worker can be re-hired later with its cwd,
    # role and conversation intact (revive-worker.sh) — killing a worker is
    # routine, and losing where it lived should not be part of the cost.
    worker_tombstone_write "$NAME" "killed"
    registry_strip_worker "$NAME"
}
with_registry_lock _kill_and_strip

echo "Killed worker '$NAME' at $TARGET (window removed from $SESSION_NAME)"
echo "Registry updated at $RT"
