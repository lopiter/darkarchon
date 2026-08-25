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
# and grok workers launch via start-worker-{codex,grok}.sh and use kind-specific
# dispatch and busy-detection paths downstream (resolved from WORKER_<sn>_KIND
# in the registry).
#
# Optional --env KEY=VALUE (repeatable) prepends env assignments to the launcher
# command line inside the new window, so the spawned agent process (and every
# Bash it runs) inherits them. Primary use: giving an orchestrator-role worker
# its own DARKARCHON_TEAM namespace for the worker team it will manage.
#
# Optional --session <name> places the worker's window in that tmux session
# instead of $SESSION_NAME (creating it if needed), while registry and state
# stay in the current team. Lets a fleet manager give each orchestrator its
# own dedicated session; the session is recorded as WORKER_<sn>_SESSION so
# kill-worker.sh recognizes the window as ours despite the session mismatch.
#
# Optional --resume-session <claude-session-id> relaunches the worker with
# `claude --resume <id>` so its previous conversation survives a reboot/kill.
# The id is recorded by lib/state-hook.sh in $STATE_DIR/states/<safe>.json.
# claude-kind workers only.
#
# Optional --spawned-by <name> records who spawned this worker (registry key
# WORKER_<sn>_SPAWNED_BY, surfaced by the dashboard as a lineage link).
# Defaults to $EE_WORKER_NAME — when an orchestrator/hermes worker calls this
# script, its own worker name is inherited automatically; a human shell has
# no EE_WORKER_NAME and records nothing.
KIND=claude
ENV_PREFIX=""
SESSION_OVERRIDE=""
RESUME_SESSION=""
SPAWNED_BY="${EE_WORKER_NAME:-}"
add_env() {
    if [[ ! "$1" =~ ^[A-Za-z_][A-Za-z0-9_]*=. ]]; then
        echo "ERROR: --env expects KEY=VALUE, got '$1'" >&2
        exit 1
    fi
    ENV_PREFIX+="$(printf '%q' "$1") "
}
while [ $# -gt 0 ]; do
    case "$1" in
        --kind)      KIND="${2:-}"; shift 2 ;;
        --kind=*)    KIND="${1#--kind=}"; shift ;;
        --env)       add_env "${2:-}"; shift 2 ;;
        --env=*)     add_env "${1#--env=}"; shift ;;
        --session)   SESSION_OVERRIDE="${2:-}"; shift 2 ;;
        --session=*) SESSION_OVERRIDE="${1#--session=}"; shift ;;
        --resume-session)   RESUME_SESSION="${2:-}"; shift 2 ;;
        --resume-session=*) RESUME_SESSION="${1#--resume-session=}"; shift ;;
        --spawned-by)   SPAWNED_BY="${2:-}"; shift 2 ;;
        --spawned-by=*) SPAWNED_BY="${1#--spawned-by=}"; shift ;;
        --)          shift; break ;;
        -*)          echo "ERROR: unknown option '$1'" >&2; exit 1 ;;
        *)           break ;;
    esac
done

if [ $# -lt 2 ]; then
    echo "Usage: $0 [--kind claude|codex|grok] [--env KEY=VALUE]... [--session <name>] [--resume-session <id>] [--spawned-by <name>] <name> <cwd> [<role>]" >&2
    exit 1
fi

if [ -n "$RESUME_SESSION" ]; then
    if [ "$KIND" != "claude" ]; then
        echo "ERROR: --resume-session is only supported for claude workers" >&2
        exit 1
    fi
    if [[ ! "$RESUME_SESSION" =~ ^[a-zA-Z0-9-]+$ ]]; then
        echo "ERROR: invalid --resume-session '$RESUME_SESSION'" >&2
        exit 1
    fi
fi
NAME="$1"
CWD="$2"
ROLE="${3:-worker}"

if [ "$KIND" != "claude" ] && [ "$KIND" != "codex" ] && [ "$KIND" != "grok" ]; then
    echo "ERROR: invalid --kind '$KIND' (expected: claude|codex|grok)" >&2
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

# The session that will HOST the window (registry/state stay on $SESSION_NAME's team).
WIN_SESSION="${SESSION_OVERRIDE:-$SESSION_NAME}"
if [ -n "$SESSION_OVERRIDE" ] && [[ ! "$SESSION_OVERRIDE" =~ ^[a-zA-Z0-9_-]+$ ]]; then
    echo "ERROR: invalid --session '$SESSION_OVERRIDE' (use only [a-zA-Z0-9_-])" >&2
    exit 1
fi
# tmux defaults to prefix matching on -t, so `myteam` would accidentally
# resolve to e.g. `myteam-other` if that exists. Use the `=name` prefix
# to force exact-match. Auto-create an empty session if it doesn't exist —
# spawn-worker is the canonical "invite to team" entrypoint, so requiring
# users to manually run `tmux new-session` first is unnecessary friction.
if ! tmux has-session -t "=$WIN_SESSION" 2>/dev/null; then
    echo "tmux session '$WIN_SESSION' not found — creating empty session." >&2
    tmux new-session -d -s "$WIN_SESSION" -c "$HOME" 2>/dev/null || {
        echo "ERROR: failed to create tmux session '$WIN_SESSION'." >&2
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
# Trailing colon forces target-session resolution: without it tmux treats the
# name as a target-window, and an orchestrator's own window in its manager's
# session (named identically to this team) makes the target ambiguous.
tmux new-window -t "=$WIN_SESSION:" -n "$NAME" -c "$CWD"

# Lock the window name so claude's runtime version string (e.g. "2.1.150" — the
# Node.js build, leaked through pane_current_command) doesn't overwrite "$NAME"
# via tmux's automatic-rename. The resolver matches `session:window_name` from
# the registry, so an auto-renamed window falls back to a target-shaped name in
# the dashboard ("myteam:2.1" instead of the chosen worker name).
tmux set-window-option -t "=$WIN_SESSION:$NAME" automatic-rename off >/dev/null
tmux set-window-option -t "=$WIN_SESSION:$NAME" allow-rename off >/dev/null

# Pass agent flags + optional TEAM_CONTEXT_DIR through env so the wrapper can pick
# it up. TEAM_CONTEXT_DIR (if set in config.env) lets the claude wrapper layer
# project-specific context on top of the generic team contract; codex ignores it
# (no launch-time prompt injection).
CTX_DIR="${TEAM_CONTEXT_DIR:-}"
if [ "$KIND" = "codex" ]; then
    LAUNCHER="$HERE/start-worker-codex.sh"
    if [ -x "$LAUNCHER" ]; then
        tmux send-keys -t "=$WIN_SESSION:$NAME" \
            "${ENV_PREFIX}CODEX_FLAGS='${CODEX_FLAGS:-}' CODEX_MODEL='${CODEX_MODEL:-}' $LAUNCHER '$NAME' '$ROLE' '$TEAM_ROOT' '$STATE_DIR' '$CTX_DIR'" Enter
    else
        # Fallback to bare codex if wrapper missing (graceful degradation)
        tmux send-keys -t "=$WIN_SESSION:$NAME" "${ENV_PREFIX}codex ${CODEX_FLAGS:---dangerously-bypass-approvals-and-sandbox}" Enter
    fi
elif [ "$KIND" = "grok" ]; then
    LAUNCHER="$HERE/start-worker-grok.sh"
    if [ -x "$LAUNCHER" ]; then
        tmux send-keys -t "=$WIN_SESSION:$NAME" \
            "${ENV_PREFIX}GROK_FLAGS='${GROK_FLAGS:-}' GROK_MODEL='${GROK_MODEL:-}' $LAUNCHER '$NAME' '$ROLE' '$TEAM_ROOT' '$STATE_DIR' '$CTX_DIR'" Enter
    else
        tmux send-keys -t "=$WIN_SESSION:$NAME" "${ENV_PREFIX}grok ${GROK_FLAGS:---always-approve}" Enter
    fi
else
    LAUNCHER="$HERE/start-worker-claude.sh"
    if [ -x "$LAUNCHER" ]; then
        tmux send-keys -t "=$WIN_SESSION:$NAME" \
            "${ENV_PREFIX}CLAUDE_FLAGS='$CLAUDE_FLAGS' $LAUNCHER '$NAME' '$ROLE' '$TEAM_ROOT' '$STATE_DIR' '$CTX_DIR' '$RESUME_SESSION'" Enter
    else
        # Fallback to bare claude if wrapper missing (graceful degradation)
        tmux send-keys -t "=$WIN_SESSION:$NAME" "${ENV_PREFIX}claude $CLAUDE_FLAGS" Enter
    fi
fi

TARGET="$WIN_SESSION:$NAME"

# tmux's immutable handle for the window. Recorded so the resolver can identify
# this pane even if the name lock above is ever lifted or overridden — an id
# never changes, a name only mostly doesn't.
WINDOW_ID="$(tmux display-message -p -t "=$WIN_SESSION:$NAME" '#{window_id}' 2>/dev/null || true)"

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
        if [ -n "$WINDOW_ID" ]; then
            printf 'WORKER_%s_WINDOW_ID=%q\n' "$SAFE" "$WINDOW_ID"
        fi
        printf 'WORKER_%s_DIR=%q\n'    "$SAFE" "$CWD"
        printf 'WORKER_%s_ROLE=%q\n'   "$SAFE" "$ROLE"
        printf 'WORKER_%s_KIND=%q\n'   "$SAFE" "$KIND"
        if [ -n "$SPAWNED_BY" ]; then
            printf 'WORKER_%s_SPAWNED_BY=%q\n' "$SAFE" "$SPAWNED_BY"
        fi
        # Record a dedicated host session so kill-worker.sh can tell "ours,
        # just in its own session" apart from an invited external pane.
        if [ -n "$SESSION_OVERRIDE" ]; then
            printf 'WORKER_%s_SESSION=%q\n' "$SAFE" "$WIN_SESSION"
        fi
    } >> "$STATE_DIR/workers-runtime.env"

    # Record the calling pane as this team's orchestrator so the dashboard
    # can group it with its workers even before any dispatch has been issued.
    if [ -n "${TMUX_PANE:-}" ]; then
        local _orch _orch_wid
        _orch="$(tmux display-message -p -t "$TMUX_PANE" '#{session_name}:#{window_index}.#{pane_index}' 2>/dev/null || true)"
        # Caller's window id, not the new worker's — indices get reused.
        _orch_wid="$(tmux display-message -p -t "$TMUX_PANE" '#{window_id}' 2>/dev/null || true)"
        if [ -n "$_orch" ]; then
            if [ -n "$_orch_wid" ]; then
                printf '%s %s\n' "$_orch" "$_orch_wid" > "$STATE_DIR/orchestrator.txt"
            else
                printf '%s\n' "$_orch" > "$STATE_DIR/orchestrator.txt"
            fi
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
elif [ "$KIND" = "grok" ]; then
    echo "Wait ~10s for grok to start. Ensure grok is logged in (~/.grok/auth.json)."
else
    echo "Wait ~15s for Claude to start. If trust prompt appears, hit Enter once."
fi
echo "Then dispatch via: $HERE/dispatch.sh $NAME '<prompt>'"
echo "Stop later via:    $HERE/kill-worker.sh $NAME"
