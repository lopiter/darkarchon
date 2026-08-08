#!/usr/bin/env bash
# leave-team.sh — A worker resigns from the team, on its own initiative.
#
# Until now a worker had no way to end its own membership. When its context
# filled up, the only available move was to die and leave a registration nobody
# could dispatch to — the team found out by trying to give it work. This is the
# resignation letter: it deregisters the worker, records where to find it again,
# and leaves a handover note for whoever takes over.
#
# Run BY the worker, in its own pane:
#   leave-team.sh --handover "what I was doing, what's left, what to watch for"
#   leave-team.sh --handover-file NOTES.md --reason context-full
#   echo "..." | leave-team.sh --handover -
#
# Identity comes from $EE_WORKER_NAME (stamped at spawn). --from names it
# explicitly, for an invited worker or a human running this on someone's behalf.
#
# The handover is NOT decoration: `revive-worker.sh <name> --fresh` layers it
# into the replacement's system prompt at launch, so the next worker starts
# knowing where this one stopped. That path exists because resuming a
# context-full conversation just replays it into the same wall.
#
# The pane is never touched — the worker keeps running and can finish its
# sentence. Exiting afterwards is the worker's own business.
#
# Exit codes:
#   0  left the team
#   1  bad args / unknown worker / no identity
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/_lib.sh"

NAME="${EE_WORKER_NAME:-}"
REASON="context-full"
HANDOVER=""
HAVE_HANDOVER=0
QUIET_NOTIFY=0
while [ $# -gt 0 ]; do
    case "$1" in
        --from)           NAME="${2:-}"; shift 2 ;;
        --reason)         REASON="${2:-}"; shift 2 ;;
        --handover)       HANDOVER="${2:-}"; HAVE_HANDOVER=1; shift 2 ;;
        --handover-file)  HANDOVER="$(cat "${2:?--handover-file needs a path}")"; HAVE_HANDOVER=1; shift 2 ;;
        --no-notify)      QUIET_NOTIFY=1; shift ;;
        -h|--help)
            sed -n '2,/^set -euo/p' "$0" | sed 's/^# \?//' | head -n -1
            exit 0 ;;
        -*) echo "ERROR: unknown option '$1'" >&2; exit 1 ;;
        *)  NAME="$1"; shift ;;
    esac
done

# `--handover -` reads the note from stdin (heredocs beat shell quoting for
# anything longer than a sentence).
if [ "$HANDOVER" = "-" ]; then
    HANDOVER="$(cat)"
fi

if [ -z "$NAME" ]; then
    echo "ERROR: don't know who is leaving — set \$EE_WORKER_NAME or pass --from <name>" >&2
    exit 1
fi
if [ -z "$(worker_target "$NAME")" ]; then
    echo "ERROR: '$NAME' is not registered in team '$SESSION_NAME' — nothing to leave" >&2
    exit 1
fi
if [ "$HAVE_HANDOVER" -eq 0 ]; then
    echo "WARNING: leaving without a handover. The next worker on '$NAME' will start blind." >&2
fi

SAFE="$(safe_name "$NAME")"

# ── Handover note ───────────────────────────────────────────────────────────
HANDOVER_FILE=""
if [ -n "$HANDOVER" ]; then
    mkdir -p "$STATE_DIR/handovers"
    HANDOVER_FILE="$STATE_DIR/handovers/$SAFE.md"
    {
        echo "## Handover from the previous '$NAME' ($(date -u +%FT%TZ), reason: $REASON)"
        echo
        echo "You are picking up where another worker of this name left off. This is"
        echo "what they wrote on their way out — it is their account, not verified"
        echo "fact, so check anything load-bearing before you rely on it."
        echo
        printf '%s\n' "$HANDOVER"
    } > "$HANDOVER_FILE"
fi

# ── Leave ───────────────────────────────────────────────────────────────────
_leave() {
    worker_tombstone_write "$NAME" "left:$REASON"
    registry_strip_worker "$NAME"
}
with_registry_lock _leave

# ── Tell the orchestrator ───────────────────────────────────────────────────
# Filed on the question queue, the existing pull channel for "a worker needs
# attention". A departure that nobody notices is how a ghost registration
# becomes a mystery two days later.
if [ "$QUIET_NOTIFY" -eq 0 ] && [ -x "$HERE/ask.sh" ]; then
    NOTICE="[LEFT THE TEAM] '$NAME' has deregistered itself (reason: $REASON)."
    if [ -n "$HANDOVER_FILE" ]; then
        NOTICE="$NOTICE Handover written to $HANDOVER_FILE."
        NOTICE="$NOTICE Bring a replacement in with: revive-worker.sh '$NAME' --fresh"
    else
        NOTICE="$NOTICE No handover was left. Restore its conversation with: revive-worker.sh '$NAME'"
    fi
    "$HERE/ask.sh" --from "$NAME" "$NOTICE" >/dev/null 2>&1 || true
fi

echo "Left team '$SESSION_NAME' as '$NAME' (reason: $REASON)"
[ -n "$HANDOVER_FILE" ] && echo "  handover:      $HANDOVER_FILE"
echo "  recall record: $(worker_tombstone_path "$NAME")"
echo "  pane untouched — you are still running, just no longer dispatchable"
echo
echo "Replacement:  revive-worker.sh '$NAME' --fresh   (starts clean, reads your handover)"
echo "Same session: revive-worker.sh '$NAME'           (resumes this conversation as-is)"
