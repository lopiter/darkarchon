#!/usr/bin/env bash
# Dynamically spawn a new worker pane in $SESSION_NAME.
#
# Usage:
#   spawn-worker.sh <name> <cwd> [<role>]
#
# Effects:
#   - Creates a new tmux window named <name> in $SESSION_NAME with cwd <cwd>
#   - Starts `claude $CLAUDE_FLAGS` in it
#   - Appends WORKER_<name>_{TARGET,DIR,ROLE} to $STATE_DIR/workers-runtime.env
#     so subsequent dispatch.sh / mailbox.sh calls can target it
#
# Exit codes:
#   0 success — pane address printed to stdout
#   1 bad args / cwd missing / name already registered / session not running
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/_lib.sh"

# Optional leading --kind flag selects the agent flavor (default claude). Codex
# workers launch via start-worker-codex.sh and use codex-specific dispatch and
# busy-detection paths downstream (resolved from WORKER_<sn>_KIND in the registry).
KIND=claude
while [ $# -gt 0 ]; do
    case "$1" in
        --kind)   KIND="${2:-}"; shift 2 ;;
        --kind=*) KIND="${1#--kind=}"; shift ;;
        --)       shift; break ;;
        -*)       echo "ERROR: unknown option '$1'" >&2; exit 1 ;;
        *)        break ;;
    esac
done

if [ $# -lt 2 ]; then
    echo "Usage: $0 [--kind claude|codex] <name> <cwd> [<role>]" >&2
    exit 1
fi
NAME="$1"
CWD="$2"
ROLE="${3:-worker}"

if [ "$KIND" != "claude" ] && [ "$KIND" != "codex" ]; then
    echo "ERROR: invalid --kind '$KIND' (expected: claude|codex)" >&2
    exit 1
fi

# Sanity checks
if [[ ! "$NAME" =~ ^[a-zA-Z0-9_-]+$ ]]; then
    echo "ERROR: invalid name '$NAME' (use only [a-zA-Z0-9_-])" >&2
    exit 1
fi
if [ ! -d "$CWD" ]; then
    echo "ERROR: cwd not found: $CWD" >&2
    exit 1
fi
# Normalize to an absolute path so cwd-collision detection compares apples to
# apples (workers_sharing_dir does an exact string match on stored DIR).
CWD="$(cd "$CWD" && pwd)"
# tmux defaults to prefix matching on -t, so `myteam` would accidentally
# resolve to e.g. `myteam-other` if that exists. Use the `=name` prefix
# to force exact-match. Auto-create an empty session if it doesn't exist —
# spawn-worker is the canonical "invite to team" entrypoint, so requiring
# users to manually run `tmux new-session` first is unnecessary friction.
if ! tmux has-session -t "=$SESSION_NAME" 2>/dev/null; then
    echo "tmux session '$SESSION_NAME' not found — creating empty session." >&2
    tmux new-session -d -s "$SESSION_NAME" -c "$HOME" 2>/dev/null || {
        echo "ERROR: failed to create tmux session '$SESSION_NAME'." >&2
        exit 1
    }
fi

EXISTING="$(worker_target "$NAME")"
if [ -n "$EXISTING" ]; then
    echo "ERROR: worker '$NAME' already registered as $EXISTING" >&2
    exit 1
fi

# cwd-collision warning: if another worker already owns this cwd (e.g. spawning a
# codex worker on the same repo as an existing claude worker), warn that their
# edits will be serialized — dispatch-safe.sh refuses a dispatch while a same-cwd
# peer is busy, to avoid two agents racing on one git working tree.
SHARED_CWD="$(workers_sharing_dir "$CWD" "$NAME" | tr '\n' ' ' | sed 's/ *$//')"
if [ -n "$SHARED_CWD" ]; then
    echo "WARNING: cwd '$CWD' is already used by worker(s): $SHARED_CWD" >&2
    echo "         Dispatches across workers sharing a cwd are serialized" >&2
    echo "         (a busy same-cwd peer causes dispatch-safe to refuse)." >&2
fi

# Create window + start claude with team contract injected as system prompt.
# Wrapper reads $TEAM_ROOT/prompts/{all.md, <role>.md or worker.md} and exports
# EE_WORKER_NAME / EE_TEAM_ROOT / EE_STATE_DIR / EE_ROLE for the worker process.
tmux new-window -t "=$SESSION_NAME" -n "$NAME" -c "$CWD"

# Lock the window name so claude's runtime version string (e.g. "2.1.150" — the
# Node.js build, leaked through pane_current_command) doesn't overwrite "$NAME"
# via tmux's automatic-rename. The resolver matches `session:window_name` from
# the registry, so an auto-renamed window falls back to a target-shaped name in
# the dashboard ("myteam:2.1" instead of the chosen worker name).
tmux set-window-option -t "=$SESSION_NAME:$NAME" automatic-rename off >/dev/null
tmux set-window-option -t "=$SESSION_NAME:$NAME" allow-rename off >/dev/null

# Pass agent flags + optional TEAM_CONTEXT_DIR through env so the wrapper can pick
# it up. TEAM_CONTEXT_DIR (if set in config.env) lets the claude wrapper layer
# project-specific context on top of the generic team contract; codex ignores it
# (no launch-time prompt injection).
CTX_DIR="${TEAM_CONTEXT_DIR:-}"
if [ "$KIND" = "codex" ]; then
    LAUNCHER="$HERE/start-worker-codex.sh"
    if [ -x "$LAUNCHER" ]; then
        tmux send-keys -t "=$SESSION_NAME:$NAME" \
            "CODEX_FLAGS='${CODEX_FLAGS:-}' CODEX_MODEL='${CODEX_MODEL:-}' $LAUNCHER '$NAME' '$ROLE' '$TEAM_ROOT' '$STATE_DIR' '$CTX_DIR'" Enter
    else
        # Fallback to bare codex if wrapper missing (graceful degradation)
        tmux send-keys -t "=$SESSION_NAME:$NAME" "codex ${CODEX_FLAGS:---dangerously-bypass-approvals-and-sandbox}" Enter
    fi
else
    LAUNCHER="$HERE/start-worker-claude.sh"
    if [ -x "$LAUNCHER" ]; then
        tmux send-keys -t "=$SESSION_NAME:$NAME" \
            "CLAUDE_FLAGS='$CLAUDE_FLAGS' $LAUNCHER '$NAME' '$ROLE' '$TEAM_ROOT' '$STATE_DIR' '$CTX_DIR'" Enter
    else
        # Fallback to bare claude if wrapper missing (graceful degradation)
        tmux send-keys -t "=$SESSION_NAME:$NAME" "claude $CLAUDE_FLAGS" Enter
    fi
fi

TARGET="$SESSION_NAME:$NAME"

SAFE="$(safe_name "$NAME")"

# Persist runtime registration under a lock so concurrent spawn/invite/kill
# don't corrupt workers-runtime.env or race on the orchestrator marker.
_persist_spawn_registration() {
    mkdir -p "$STATE_DIR"
    {
        echo ""
        echo "# spawned $(date -u +%FT%TZ)  name=$NAME"
        printf 'WORKER_%s_NAME=%q\n'   "$SAFE" "$NAME"
        printf 'WORKER_%s_TARGET=%q\n' "$SAFE" "$TARGET"
        printf 'WORKER_%s_DIR=%q\n'    "$SAFE" "$CWD"
        printf 'WORKER_%s_ROLE=%q\n'   "$SAFE" "$ROLE"
        printf 'WORKER_%s_KIND=%q\n'   "$SAFE" "$KIND"
    } >> "$STATE_DIR/workers-runtime.env"

    # Record the calling pane as this team's orchestrator so the dashboard
    # can group it with its workers even before any dispatch has been issued.
    if [ -n "${TMUX_PANE:-}" ]; then
        local _orch
        _orch="$(tmux display-message -p -t "$TMUX_PANE" '#{session_name}:#{window_index}.#{pane_index}' 2>/dev/null || true)"
        if [ -n "$_orch" ]; then
            printf '%s\n' "$_orch" > "$STATE_DIR/orchestrator.txt"
        fi
    fi
}
with_registry_lock _persist_spawn_registration

echo "Spawned worker '$NAME'"
echo "  target: $TARGET"
echo "  cwd:    $CWD"
echo "  role:   $ROLE"
echo "  kind:   $KIND"
echo
if [ "$KIND" = "codex" ]; then
    echo "Wait ~10s for codex to start. Ensure 'codex login' is done (else 401)."
else
    echo "Wait ~15s for Claude to start. If trust prompt appears, hit Enter once."
fi
echo "Then dispatch via: $HERE/dispatch.sh $NAME '<prompt>'"
echo "Stop later via:    $HERE/kill-worker.sh $NAME"
