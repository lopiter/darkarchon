#!/usr/bin/env bash
# check-worker-state.sh — Report worker state without dispatching.
#
# Thin wrapper over lib/worker_state.py, the single state resolver shared with
# dispatch-safe.sh and the dashboard agent. All detection logic (hook events >
# TUI scraping, liveness) lives in the resolver — this script only adds the
# running-task annotation and the unknown-worker guard, and keeps the stable
# command name orchestrators call.
#
# Usage:
#   check-worker-state.sh <worker>             # one-line state
#   check-worker-state.sh <worker> --verbose   # + last 15 pane lines
#
# Exit codes:
#   0  — success (any state)
#   1  — bad args / unknown worker / session not running
#
# Output (one line, KEY=VALUE): state=<...> worker=<...> target=<...>
#   kind=<...> [detail='...'] source=<hook|scrape|...> [task=<id>]
# `state` uses the resolver's vocabulary (idle|busy|unsent|awaiting_user|
# compacting|rate_limited|error|dead|unknown). `task=` is a running dispatch id
# (informational only — the resolved pane/hook state is authoritative).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/lib/_lib.sh"

VERBOSE=0
WORKER=""
for arg in "$@"; do
    case "$arg" in
        --verbose|-v) VERBOSE=1 ;;
        -h|--help)
            sed -n '2,/^set -euo/p' "$0" | sed 's/^# \?//' | head -n -1
            exit 0
            ;;
        -*) echo "Unknown flag: $arg" >&2; exit 1 ;;
        *) WORKER="$arg" ;;
    esac
done

if [ -z "$WORKER" ]; then
    echo "Usage: $0 <worker> [--verbose]" >&2
    exit 1
fi

TARGET="$(worker_target "$WORKER")"
if [ -z "$TARGET" ]; then
    echo "ERROR: unknown worker '$WORKER'" >&2
    echo "Known: $(all_known_workers | tr '\n' ' ')" >&2
    exit 1
fi
if ! tmux has-session -t "=${TARGET%%:*}" 2>/dev/null; then
    echo "ERROR: tmux session '${TARGET%%:*}' not running" >&2
    exit 1
fi

# Running dispatch for this worker, if any (informational — resolver is truth).
RUNNING_TASK=""
if [ -x "$HERE/lib/tasks.sh" ]; then
    RUNNING_TASK="$(
        "$HERE/lib/tasks.sh" list 2>/dev/null \
            | awk -v w="$WORKER" 'NR>2 && $2 == w && $3 == "running" { print $1; exit }' \
            || true
    )"
fi

RESOLVE_ARGS=("$WORKER")
[ "$VERBOSE" = "1" ] && RESOLVE_ARGS+=(--verbose)
OUT="$(python3 "$HERE/lib/worker_state.py" "${RESOLVE_ARGS[@]}")"

if [ -n "$RUNNING_TASK" ]; then
    # Append task= to the first (KEY=VALUE) line; keep any verbose tail below it.
    FIRST="$(printf '%s\n' "$OUT" | head -1) task=$RUNNING_TASK"
    REST="$(printf '%s\n' "$OUT" | tail -n +2)"
    printf '%s\n' "$FIRST"
    [ -n "$REST" ] && printf '%s\n' "$REST"
else
    printf '%s\n' "$OUT"
fi
exit 0
