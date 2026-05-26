#!/usr/bin/env bash
# invite-worker.sh — Register an EXISTING tmux pane (running Claude) as a team worker.
#
# Use when you already have a Claude session running in another tmux session/window
# and want to dispatch tasks to it without re-spawning.
#
# Usage:
#   invite-worker.sh <name> <session:window> [<role>]
#
# Examples:
#   invite-worker.sh helper main:1 backend
#   invite-worker.sh scout 5:scout python
#
# Behavior:
#   - Verifies tmux target exists
#   - Heuristic check that pane shows a Claude prompt (warns otherwise)
#   - Auto-derives cwd from pane's current path (best effort)
#   - Marks worker EXTERNAL=1 so start.sh / kill-worker.sh skip it
#   - Appends to $STATE_DIR/workers-runtime.env (sourced by lib/_lib.sh)
#
# Removal: use uninvite-worker.sh <name> (does NOT kill the tmux pane).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/lib/_lib.sh"

if [ $# -lt 2 ]; then
    echo "Usage: $0 <name> <session:window> [<role>]" >&2
    echo "  <name>            worker handle (sanitized; matches what dispatch.sh accepts)" >&2
    echo "  <session:window>  tmux target of the existing Claude pane" >&2
    echo "  <role>            free-form label (default: worker-invited)" >&2
    exit 1
fi
NAME="$1"
TARGET="$2"
ROLE="${3:-worker-invited}"

# Sanity checks
if [[ ! "$NAME" =~ ^[a-zA-Z0-9_-]+$ ]]; then
    echo "ERROR: invalid name '$NAME' (use only [a-zA-Z0-9_-])" >&2
    exit 1
fi
if [[ ! "$TARGET" =~ ^[^:]+:[^:]+$ ]]; then
    echo "ERROR: target must be 'session:window' (got '$TARGET')" >&2
    exit 1
fi

# Already registered?
EXISTING="$(worker_target "$NAME")"
if [ -n "$EXISTING" ]; then
    echo "ERROR: worker '$NAME' already registered as $EXISTING" >&2
    echo "Use uninvite-worker.sh '$NAME' first, or pick a different name." >&2
    exit 1
fi

# Verify session exists
SESSION="${TARGET%%:*}"
WIN="${TARGET#*:}"
if ! tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "ERROR: tmux session '$SESSION' not running" >&2
    exit 1
fi
# Verify window exists by name OR index
if ! tmux list-windows -t "$SESSION" -F '#W' 2>/dev/null | grep -qx "$WIN" \
    && ! tmux list-windows -t "$SESSION" -F '#I' 2>/dev/null | grep -qx "$WIN"; then
    echo "ERROR: tmux window '$WIN' not found in session '$SESSION'" >&2
    echo "Available windows in '$SESSION':" >&2
    tmux list-windows -t "$SESSION" -F '  #I:#W' >&2
    exit 1
fi

# Derive cwd (best effort)
CWD="$(tmux display-message -p -t "$TARGET" '#{pane_current_path}' 2>/dev/null || true)"
[ -z "$CWD" ] && CWD="(unknown)"

# Heuristic: does pane look like Claude?
PANE="$(tmux capture-pane -p -t "$TARGET" -S -50 2>/dev/null || true)"
if echo "$PANE" | grep -qE 'Claude Code|bypass permissions on|Opus|Sonnet|Haiku'; then
    CLAUDE_DETECTED=yes
else
    CLAUDE_DETECTED=no
fi

# Register in runtime — single lock covers both registry append and the
# orchestrator marker write so a concurrent spawn/kill can't interleave.
SAFE="$(safe_name "$NAME")"
_persist_invite_registration() {
    mkdir -p "$STATE_DIR"
    {
        echo ""
        echo "# invited $(date -u +%FT%TZ)  name=$NAME  source=$TARGET  detected_claude=$CLAUDE_DETECTED"
        printf 'WORKER_%s_NAME=%q\n'     "$SAFE" "$NAME"
        printf 'WORKER_%s_TARGET=%q\n'   "$SAFE" "$TARGET"
        printf 'WORKER_%s_DIR=%q\n'      "$SAFE" "$CWD"
        printf 'WORKER_%s_ROLE=%q\n'     "$SAFE" "$ROLE"
        printf 'WORKER_%s_EXTERNAL=1\n'  "$SAFE"
    } >> "$STATE_DIR/workers-runtime.env"

    if [ -n "${TMUX_PANE:-}" ]; then
        local _orch
        _orch="$(tmux display-message -p -t "$TMUX_PANE" '#{session_name}:#{window_index}.#{pane_index}' 2>/dev/null || true)"
        if [ -n "$_orch" ]; then
            printf '%s\n' "$_orch" > "$STATE_DIR/orchestrator.txt"
        fi
    fi
}
with_registry_lock _persist_invite_registration

echo "Invited worker '$NAME'"
echo "  target:         $TARGET (external — not in our session $SESSION_NAME)"
echo "  cwd:            $CWD"
echo "  role:           $ROLE"
echo "  claude detected: $CLAUDE_DETECTED"
if [ "$CLAUDE_DETECTED" = "no" ]; then
    echo
    echo "  WARNING: pane content doesn't show Claude markers. Dispatch may fail."
    echo "  If the pane IS running Claude, ignore. Otherwise launch Claude there first."
fi
echo
echo "Dispatch:  $HERE/lib/dispatch.sh $NAME '<prompt>'"
echo "Uninvite:  $HERE/uninvite-worker.sh $NAME"
