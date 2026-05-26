#!/usr/bin/env bash
# Spawn the team tmux session: one window per worker listed in WORKERS.
# The orchestrator (the Claude session that invoked this script) coordinates
# the team directly; no separate "leader" pane is created.
# Reads worker definitions from config.env via _lib.sh.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/_lib.sh"

# Kill old session if exists
tmux kill-session -t "$SESSION_NAME" 2>/dev/null || true

# Pick the first non-external worker as the seed window for the new session.
# tmux requires at least one window when creating a session.
SEED_WORKER=""
for w in "${WORKERS[@]}"; do
    if worker_is_external "$w"; then
        continue
    fi
    if [ -n "$(worker_dir "$w")" ]; then
        SEED_WORKER="$w"
        break
    fi
done

if [ -z "$SEED_WORKER" ]; then
    echo "ERROR: no spawnable workers in WORKERS — nothing to start" >&2
    echo "(all entries are EXTERNAL or missing WORKER_<name>_DIR)" >&2
    exit 1
fi

tmux new-session -d -s "$SESSION_NAME" -n "$SEED_WORKER" -c "$(worker_dir "$SEED_WORKER")" -x 220 -y 50

# Remaining worker windows
for w in "${WORKERS[@]}"; do
    if worker_is_external "$w"; then
        continue
    fi
    [ "$w" = "$SEED_WORKER" ] && continue
    dir="$(worker_dir "$w")"
    if [ -z "$dir" ]; then
        echo "WARN: worker '$w' has no WORKER_${w}_DIR set, skipping" >&2
        continue
    fi
    tmux new-window -t "$SESSION_NAME" -n "$w" -c "$dir"
done

# Start claude in each worker window
for w in "${WORKERS[@]}"; do
    if worker_is_external "$w"; then
        continue
    fi
    [ -z "$(worker_dir "$w")" ] && continue
    tmux send-keys -t "$SESSION_NAME:$w" "claude $CLAUDE_FLAGS" Enter
done

echo "Spawned tmux session: $SESSION_NAME"
echo "Windows:"
tmux list-windows -t "$SESSION_NAME"
echo
echo "Wait ~15s for Claude to start in each window."
echo "If trust prompt appears in a window, hit Enter (default = trust)."
echo "To attach manually: tmux attach -t $SESSION_NAME"
