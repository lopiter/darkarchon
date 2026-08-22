#!/usr/bin/env bash
# Worker-side launcher for a Grok (xAI "Grok Build" CLI) team worker.
#
# Counterpart to start-worker-codex.sh. Called from spawn-worker.sh via tmux
# send-keys. Like codex, grok workers are v1 task-executors driven entirely by
# the per-dispatch trigger line:
#   1. No launch-time prompt injection — the team contract is NOT attached, and
#      we write nothing into the repo.
#   2. No darkarchon MCP server — grok configures MCP via `grok mcp add`
#      (~/.grok), so peer-messaging/ask are unavailable. Dispatch itself never
#      uses MCP, so task execution is unaffected.
#   3. Launch with NO positional prompt (a positional arg starts a turn); we
#      want an empty idle pane that later receives dispatches.
#
# Usage (signature mirrors start-worker-claude.sh for a uniform spawn call site):
#   start-worker-grok.sh <worker_name> <role> <team_root> <state_dir> [<context_dir>]
#
# Env knobs (passed by spawn-worker.sh from config.env):
#   GROK_FLAGS   default: --always-approve   (grok's "auto-approve all tool
#                executions"; --permission-mode bypassPermissions is the
#                finer-grained alternative)
#   GROK_MODEL   optional model id (`grok models` lists them); empty = default
#
# Exports for the worker process: EE_WORKER_NAME, EE_TEAM_ROOT, EE_STATE_DIR, EE_ROLE
set -euo pipefail

if [ $# -lt 4 ]; then
    echo "Usage: $0 <worker_name> <role> <team_root> <state_dir> [<context_dir>]" >&2
    exit 1
fi

WORKER_NAME="$1"
ROLE="$2"
TEAM_ROOT="$3"
STATE_DIR="$4"
# CONTEXT_DIR ("$5") intentionally unused — grok has no launch-time prompt injection.

export EE_WORKER_NAME="$WORKER_NAME"
export EE_TEAM_ROOT="$TEAM_ROOT"
export EE_STATE_DIR="$STATE_DIR"
export EE_ROLE="$ROLE"

# Auth pre-flight: grok keeps its login in $GROK_HOME/auth.json (default
# ~/.grok/auth.json). Without it the welcome screen asks to authenticate and
# every dispatch would sit on that dialog. Warn rather than fail — the user may
# log in after the pane is up.
GROK_HOME_DIR="${GROK_HOME:-$HOME/.grok}"
if [ ! -f "$GROK_HOME_DIR/auth.json" ]; then
    echo "WARNING: no $GROK_HOME_DIR/auth.json — grok will ask to log in." >&2
    echo "         Run 'grok' once interactively and authenticate before dispatching." >&2
fi

# Heartbeat writer tracks our pid. After exec (below) the pid is reused by grok,
# so the writer keeps tracking the live worker and self-exits when it dies.
HEARTBEAT_WRITER="$TEAM_ROOT/lib/heartbeat-writer.sh"
if [ -x "$HEARTBEAT_WRITER" ]; then
    "$HEARTBEAT_WRITER" "$WORKER_NAME" "$STATE_DIR" "$$" &
    disown
fi

# Build grok argv. Never pass a positional prompt.
GROK_FLAGS="${GROK_FLAGS:---always-approve}"
GROK_MODEL="${GROK_MODEL:-}"
GROK_ARGS="$GROK_FLAGS"
if [ -n "$GROK_MODEL" ]; then
    GROK_ARGS="$GROK_ARGS --model $GROK_MODEL"
fi

# PATH robustness: grok installs to ~/.grok/bin, which a non-login tmux pane
# may not have on PATH. Re-exec through a login shell if needed; exec keeps our
# pid either way so the heartbeat writer's tracking stays valid.
# shellcheck disable=SC2086
if command -v grok >/dev/null 2>&1; then
    exec grok $GROK_ARGS
elif [ -x "$HOME/.grok/bin/grok" ]; then
    exec "$HOME/.grok/bin/grok" $GROK_ARGS
else
    exec "${SHELL:-/bin/zsh}" -lc "exec grok $GROK_ARGS"
fi
