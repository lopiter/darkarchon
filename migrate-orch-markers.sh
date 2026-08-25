#!/usr/bin/env bash
# migrate-orch-markers.sh — Rewrite orchestrator.txt to window-id form.
#
# spawn/invite used to store `session:window.pane`. Window indices are reused
# after a respawn, so a stale marker can badge the wrong pane. New markers
# store `session:window.pane @id`. This walks every team dir and:
#   - rewrites a live index-form marker to include window_id
#   - deletes a marker whose pane (or recorded window_id) is gone
#   - skips (does not delete) lookups that look like tmux active-window fallback
#
# Usage:
#   migrate-orch-markers.sh           # dry-run (default) — print plan only
#   migrate-orch-markers.sh --apply   # write/delete
#   migrate-orch-markers.sh --root DIR
#
# Exit codes:
#   0  ok
#   1  bad args
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/lib/_lib.sh"

APPLY=()
ROOT=("$HOST_STATE_DIR")
while [ $# -gt 0 ]; do
    case "$1" in
        --apply)      APPLY=(--apply); shift ;;
        --dry-run|-n) shift ;;  # default; accepted for prune-workers muscle memory
        --root)       ROOT=("$2"); shift 2 ;;
        --root=*)     ROOT=("${1#--root=}"); shift ;;
        -h|--help)
            sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) echo "ERROR: unknown argument '$1'" >&2; exit 1 ;;
    esac
done

exec python3 "$HERE/lib/migrate_orch_markers.py" --root "${ROOT[0]}" "${APPLY[@]+"${APPLY[@]}"}"
