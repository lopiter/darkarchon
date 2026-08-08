#!/usr/bin/env bash
# prune-workers.sh — Drop dead workers from the team registry.
#
# A worker that died (reboot, killed session, context exhausted) leaves its
# registration behind. The name stays taken, so the same worker cannot be
# spawned or invited again, and every dispatch to it is refused. This clears
# those ghosts — and ONLY those: no tmux window is ever touched, so a pane that
# someone has since relaunched their own agent in survives the cleanup.
#
# Usage:
#   prune-workers.sh              # list dead workers, then prune with confirmation
#   prune-workers.sh --dry-run    # list only, change nothing
#   prune-workers.sh --yes        # prune without asking (for scripts/agents)
#
# Exit codes:
#   0  pruned (or nothing to prune)
#   1  bad args
#   2  aborted at the confirmation prompt
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/lib/_lib.sh"

DRY_RUN=0
ASSUME_YES=0
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run|-n) DRY_RUN=1; shift ;;
        --yes|-y)     ASSUME_YES=1; shift ;;
        -h|--help)
            sed -n '2,/^set -euo/p' "$0" | sed 's/^# \?//' | head -n -1
            exit 0 ;;
        *) echo "ERROR: unknown argument '$1'" >&2; exit 1 ;;
    esac
done

DEAD=()
ORPHANED=()
while IFS= read -r w; do
    [ -n "$w" ] || continue
    STATE="$(python3 "$HERE/lib/worker_state.py" "$w" --field state 2>/dev/null || true)"
    [ "$STATE" = "dead" ] || continue
    DEAD+=("$w")
    if [ "$(python3 "$HERE/lib/worker_state.py" "$w" --field orphaned 2>/dev/null || true)" = "1" ]; then
        ORPHANED+=("$w")
    fi
done < <(all_known_workers)

if [ "${#DEAD[@]}" -eq 0 ]; then
    echo "No dead workers — nothing to prune."
    exit 0
fi

echo "Dead workers in team '$SESSION_NAME':"
for w in "${DEAD[@]}"; do
    TARGET="$(worker_target "$w")"
    NOTE=""
    for o in "${ORPHANED[@]:-}"; do
        [ "$o" = "$w" ] && NOTE="   [pane occupied by an unregistered agent — window left alone]"
    done
    echo "  - $w ($TARGET)$NOTE"
done
echo

if [ "$DRY_RUN" -eq 1 ]; then
    echo "--dry-run: nothing changed."
    exit 0
fi

if [ "$ASSUME_YES" -eq 0 ]; then
    printf 'Deregister these %d worker(s)? No tmux window will be killed. [y/N] ' "${#DEAD[@]}"
    read -r REPLY </dev/tty || REPLY=""
    case "$REPLY" in
        y|Y|yes|YES) : ;;
        *) echo "Aborted."; exit 2 ;;
    esac
fi

for w in "${DEAD[@]}"; do
    "$HERE/lib/deregister-worker.sh" "$w" --quiet
    echo "pruned: $w"
done

echo
echo "Pruned ${#DEAD[@]} worker(s). Panes untouched."
if [ "${#ORPHANED[@]}" -gt 0 ]; then
    echo "Note: ${ORPHANED[*]} had a live agent in its window — that session is still"
    echo "      running and is now unmanaged. Adopt it with: revive-worker.sh <name> --adopt"
fi
echo "To bring a pruned worker back with its conversation: revive-worker.sh <name>"
