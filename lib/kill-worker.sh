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
_strip_worker_from_registry() {
    [ -f "$RT" ] || return 0
    local tmp="$RT.tmp.$$"
    awk -v sn="$SAFE" '
        BEGIN { drop_comment = 0 }
        /^# spawned / { last_comment = $0; drop_comment = 0; next }
        $0 ~ ("^WORKER_" sn "_") {
            # drop this line, and also the most recent comment header (one-shot).
            drop_comment = 1
            next
        }
        {
            if (last_comment != "" && drop_comment == 0) {
                print last_comment
            }
            last_comment = ""
            drop_comment = 0
            print
        }
        END {
            if (last_comment != "" && drop_comment == 0) print last_comment
        }
    ' "$RT" > "$tmp"
    mv "$tmp" "$RT"
}
with_registry_lock _strip_worker_from_registry

echo "Killed worker '$NAME' at $TARGET (window removed from $SESSION_NAME)"
echo "Registry updated at $RT"
