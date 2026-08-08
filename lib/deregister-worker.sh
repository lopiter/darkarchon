#!/usr/bin/env bash
# deregister-worker.sh — Remove a worker from the runtime registry, leaving its
# tmux pane completely untouched.
#
# The primitive the other removal commands were missing. `kill-worker.sh` kills
# the window before it deregisters, and `uninvite-worker.sh` only accepts
# EXTERNAL workers — so there was no way to drop the registration of a SPAWNED
# worker whose pane is still in use. That gap is a footgun: when a worker's
# claude is killed and the user relaunches their own claude in the same window,
# the only cleanup command available (kill-worker) destroys that new session.
#
# Usage:
#   deregister-worker.sh <name> [--force] [--quiet]
#
# Refuses when the worker resolves to a live state, since deregistering a
# working worker strands it (it keeps running, nobody can dispatch to it).
# --force overrides — used by revive-worker.sh, which re-registers immediately
# after, and available for a wedged resolver.
#
# Exit codes:
#   0  deregistered
#   1  bad args / unknown worker
#   2  refused: worker is alive (use --force, or kill-worker.sh to end it)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/_lib.sh"

FORCE=0
QUIET=0
NAME=""
while [ $# -gt 0 ]; do
    case "$1" in
        --force) FORCE=1; shift ;;
        --quiet) QUIET=1; shift ;;
        -h|--help)
            sed -n '2,/^set -euo/p' "$0" | sed 's/^# \?//' | head -n -1
            exit 0 ;;
        -*) echo "ERROR: unknown option '$1'" >&2; exit 1 ;;
        *)  NAME="$1"; shift ;;
    esac
done

if [ -z "$NAME" ]; then
    echo "Usage: $0 <name> [--force] [--quiet]" >&2
    exit 1
fi

TARGET="$(worker_target "$NAME")"
if [ -z "$TARGET" ]; then
    echo "ERROR: no worker '$NAME' registered" >&2
    echo "Known: $(all_known_workers | tr '\n' ' ')" >&2
    exit 1
fi

# Liveness guard. States that mean "an agent is running and reachable" are
# refused; dead/unknown are exactly what this command exists to clean up.
# A resolver failure must not block cleanup, so an unreadable state falls
# through to allowed.
if [ "$FORCE" -eq 0 ]; then
    STATE="$(python3 "$HERE/worker_state.py" "$NAME" --field state 2>/dev/null || true)"
    case "$STATE" in
        dead|unknown|"") : ;;
        *)
            echo "REFUSED: worker '$NAME' ($TARGET) is $STATE — deregistering a live" >&2
            echo "  worker strands it: it keeps running with nobody able to dispatch." >&2
            echo "  End it with kill-worker.sh, or pass --force if you mean to detach it." >&2
            exit 2 ;;
    esac
fi

# Snapshot first: once the registration is gone, so is the cwd/role a revive
# would need. Runs inside the lock so it can't read a half-rewritten registry.
_deregister() {
    worker_tombstone_write "$NAME" "${DEREGISTER_REASON:-deregistered}"
    registry_strip_worker "$NAME"
}
with_registry_lock _deregister

if [ "$QUIET" -eq 0 ]; then
    echo "Deregistered worker '$NAME' (was: $TARGET)"
    echo "  tmux pane left untouched — nothing was killed"
    echo "  registry: $STATE_DIR/workers-runtime.env"
    echo "  recall record: $(worker_tombstone_path "$NAME")"
    echo
    echo "Bring it back later with: revive-worker.sh '$NAME'"
fi
