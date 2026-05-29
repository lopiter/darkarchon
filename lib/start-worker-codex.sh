#!/usr/bin/env bash
# Worker-side launcher for a Codex (OpenAI codex CLI) team worker.
#
# Counterpart to start-worker-claude.sh. Called from spawn-worker.sh via tmux
# send-keys. Codex differs from Claude in three ways that shape this launcher:
#   1. No `--append-system-prompt` — the team contract is NOT injected at launch.
#      v1 codex workers are task-executors driven entirely by the per-dispatch
#      trigger line, so we deliberately write nothing into the repo (no AGENTS.md).
#   2. No `--mcp-config` — codex configures MCP via ~/.codex/config.toml, so the
#      darkarchon MCP server is not attached. Peer-messaging/ask are unavailable
#      in v1 (dispatch itself never uses MCP, so task execution is unaffected).
#   3. Launch with NO positional prompt (a positional arg would start a session);
#      we want an empty idle pane that later receives dispatches.
#
# Usage (signature mirrors start-worker-claude.sh for a uniform spawn call site;
# role/team_root/context_dir are accepted but only used for EE_* env exports):
#   start-worker-codex.sh <worker_name> <role> <team_root> <state_dir> [<context_dir>]
#
# Env knobs (passed by spawn-worker.sh from config.env):
#   CODEX_FLAGS   default: --dangerously-bypass-approvals-and-sandbox
#   CODEX_MODEL   optional model name; empty = codex default
#
# Exports for the worker process (future legacy-fallback peer support; harmless now):
#   EE_WORKER_NAME, EE_TEAM_ROOT, EE_STATE_DIR, EE_ROLE
set -euo pipefail

if [ $# -lt 4 ]; then
    echo "Usage: $0 <worker_name> <role> <team_root> <state_dir> [<context_dir>]" >&2
    exit 1
fi

WORKER_NAME="$1"
ROLE="$2"
TEAM_ROOT="$3"
STATE_DIR="$4"
# CONTEXT_DIR ("$5") intentionally unused — codex has no launch-time prompt injection.

export EE_WORKER_NAME="$WORKER_NAME"
export EE_TEAM_ROOT="$TEAM_ROOT"
export EE_STATE_DIR="$STATE_DIR"
export EE_ROLE="$ROLE"

# Auth pre-flight: codex needs ~/.codex/auth.json (ChatGPT login) or OPENAI_API_KEY.
# Without it codex starts but every turn fails with "Failed to refresh token: 401".
# Warn loudly rather than failing — the user may log in after the pane is up.
if [ ! -f "$HOME/.codex/auth.json" ] && [ -z "${OPENAI_API_KEY:-}" ]; then
    echo "WARNING: no ~/.codex/auth.json and OPENAI_API_KEY unset — codex will 401." >&2
    echo "         Run 'codex login' (or export OPENAI_API_KEY) before dispatching." >&2
fi

# Heartbeat writer tracks our pid. After exec (below) the pid is reused by codex,
# so the writer keeps tracking the live worker and self-exits when it dies.
HEARTBEAT_WRITER="$TEAM_ROOT/lib/heartbeat-writer.sh"
if [ -x "$HEARTBEAT_WRITER" ]; then
    "$HEARTBEAT_WRITER" "$WORKER_NAME" "$STATE_DIR" "$$" &
    disown
fi

# Build codex argv. Never pass a positional prompt.
CODEX_FLAGS="${CODEX_FLAGS:---dangerously-bypass-approvals-and-sandbox}"
CODEX_MODEL="${CODEX_MODEL:-}"
CODEX_ARGS="$CODEX_FLAGS"
if [ -n "$CODEX_MODEL" ]; then
    CODEX_ARGS="$CODEX_ARGS --model $CODEX_MODEL"
fi

# PATH robustness: tmux panes are often non-login shells, so brew/nvm-installed
# codex may not be on PATH. If codex isn't directly resolvable, re-exec through a
# login shell so rc files populate PATH. exec preserves our pid either way, so the
# heartbeat writer's pid tracking stays valid.
# shellcheck disable=SC2086
if command -v codex >/dev/null 2>&1; then
    exec codex $CODEX_ARGS
else
    exec "${SHELL:-/bin/zsh}" -lc "exec codex $CODEX_ARGS"
fi
