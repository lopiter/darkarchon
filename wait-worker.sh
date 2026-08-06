#!/usr/bin/env bash
# wait-worker.sh — block until a worker reaches one of the given states.
#
# The orchestrator-side wait primitive (herdr's `agent wait <t> --until STATUS`,
# ported): dispatch, then wait for "settled" instead of polling pane text in ad
# hoc loops. Defaults to the settled set — done (idle) or needs a human
# (awaiting_permission / awaiting_user), so a worker stuck on a permission
# prompt surfaces immediately instead of looking busy until timeout.
#
# Usage:
#   wait-worker.sh <worker> [--until s1[,s2...]] [--timeout SEC] [--interval SEC] [--json]
#
# States: idle busy compacting awaiting_permission awaiting_user unsent
#         rate_limited error dead unknown
#
# Exit codes:
#   0  reached  — worker is in one of the requested states
#   1  unknown  — worker not in the registry (or bad args)
#   2  timeout  — deadline hit before any requested state was seen
#   3  dead     — worker died while waiting (and 'dead' was not requested)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/lib/_lib.sh"

if [ $# -lt 1 ] || [[ "$1" == -* ]]; then
    echo "Usage: $0 <worker> [--until s1[,s2...]] [--timeout SEC] [--interval SEC] [--json]" >&2
    exit 1
fi

WORKER="$1"; shift

HAS_UNTIL=0
for a in "$@"; do [ "$a" = "--until" ] && HAS_UNTIL=1; done

if [ "$HAS_UNTIL" -eq 1 ]; then
    exec python3 "$HERE/lib/worker_state.py" "$WORKER" "$@"
fi
exec python3 "$HERE/lib/worker_state.py" "$WORKER" \
    --until "idle,awaiting_permission,awaiting_user" "$@"
