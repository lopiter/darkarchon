#!/usr/bin/env bash
# uninvite-worker.sh — Remove an invited (external) worker from the runtime registry.
#
# Does NOT touch the tmux pane — invited workers are externally owned (the user
# launched them), so we never kill them. Only the registry entry is dropped so
# dispatch.sh no longer routes to them.
#
# Usage:
#   uninvite-worker.sh <name>
#
# Refuses if the named worker is not EXTERNAL (use kill-worker.sh for those).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/lib/_lib.sh"

if [ $# -lt 1 ]; then
    echo "Usage: $0 <name>" >&2
    exit 1
fi
NAME="$1"

TARGET="$(worker_target "$NAME")"
if [ -z "$TARGET" ]; then
    echo "ERROR: no worker '$NAME' registered" >&2
    exit 1
fi

# Refuse non-external (would be a misuse — direct user to kill-worker.sh)
if ! worker_is_external "$NAME"; then
    echo "ERROR: '$NAME' is not an invited/external worker." >&2
    echo "Use lib/kill-worker.sh for spawned workers (it tears down the tmux window too)." >&2
    exit 1
fi

SAFE="$(safe_name "$NAME")"
RT="$STATE_DIR/workers-runtime.env"
if [ ! -f "$RT" ]; then
    echo "ERROR: runtime registry not found at $RT" >&2
    exit 1
fi

# Strip under a lock so a concurrent spawn/invite isn't lost by the rewrite.
_strip_invited_from_registry() {
    local tmp="$RT.tmp.$$"
    awk -v sn="$SAFE" '
        BEGIN { drop_comment = 0 }
        /^# (spawned|invited) / { last_comment = $0; drop_comment = 0; next }
        $0 ~ ("^WORKER_" sn "_") {
            drop_comment = 1
            next
        }
        {
            if (last_comment != "" && drop_comment == 0) print last_comment
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
with_registry_lock _strip_invited_from_registry

echo "Uninvited worker '$NAME' (was: $TARGET)"
echo "  tmux pane left untouched (external — user-owned)"
echo "  registry: $RT"
