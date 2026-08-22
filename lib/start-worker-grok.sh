#!/usr/bin/env bash
# Worker-side launcher for a Grok (xAI "Grok Build" CLI) team worker.
#
# Counterpart to start-worker-claude.sh, using what grok offers instead of
# Claude's flags (all live-verified on grok 1.0.5):
#   1. Team contract via `--rules` (appends to the system prompt). Same prompt
#      layers as the claude launcher plus prompts/grok.md, which swaps the
#      mcp__darkarchon__* tools for their shell equivalents — grok has no
#      per-launch MCP flag, but it inherits EE_* from this process, so
#      lib/ask.sh and lib/mailbox.sh already know who is calling.
#   2. Lifecycle hooks via a global ~/.grok/hooks/darkarchon.json (installed
#      here, idempotently; grok has no per-launch hooks flag). The receiver,
#      lib/grok-state-hook.sh, no-ops outside a worker's environment, so the
#      user's own grok sessions are unaffected.
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
CONTEXT_DIR="${5:-}"

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

# ── Prompt layers (same stack as start-worker-claude.sh) ───────────────────
PROMPT=""
append_layer() {
    local f="$1"
    if [ -f "$f" ]; then
        if [ -z "$PROMPT" ]; then
            PROMPT="$(<"$f")"
        else
            PROMPT="${PROMPT}

---

$(<"$f")"
        fi
    fi
}
append_layer "$TEAM_ROOT/prompts/all.md"
[ -n "$CONTEXT_DIR" ] && append_layer "$CONTEXT_DIR/all.md"
ROLE_FILE="$TEAM_ROOT/prompts/${ROLE}.md"
if [ -f "$ROLE_FILE" ]; then
    append_layer "$ROLE_FILE"
else
    append_layer "$TEAM_ROOT/prompts/worker.md"
fi
[ -n "$CONTEXT_DIR" ] && append_layer "$CONTEXT_DIR/${ROLE}.md"
# grok overlay LAST so its tool substitutions override the contract's MCP names.
append_layer "$TEAM_ROOT/prompts/grok.md"
# Handover from a previous holder of this name (see leave-team.sh). Consumed once.
HANDOVER_FILE="$STATE_DIR/handovers/$(printf '%s' "$WORKER_NAME" | tr -c '[:alnum:]_' '_').md"
if [ -f "$HANDOVER_FILE" ]; then
    append_layer "$HANDOVER_FILE"
    mv "$HANDOVER_FILE" "${HANDOVER_FILE%.md}.consumed.md" 2>/dev/null || true
fi
PROMPT="${PROMPT}

---

## Runtime Context (this worker)

- EE_WORKER_NAME=${WORKER_NAME}
- EE_ROLE=${ROLE}
- EE_TEAM_ROOT=${TEAM_ROOT}
- EE_STATE_DIR=${STATE_DIR}
- cwd=$(pwd)
"

# ── Lifecycle hooks (global file, idempotent) ──────────────────────────────
# grok merges every ~/.grok/hooks/*.json, so darkarchon owns exactly one file
# there and never touches others (the same pattern herdr uses). The receiver
# bails out unless EE_WORKER_NAME/EE_STATE_DIR are set, which only a spawned
# worker's process tree has — grok hands the session env to every hook.
STATE_HOOK="$TEAM_ROOT/lib/grok-state-hook.sh"
if [ -x "$STATE_HOOK" ]; then
    mkdir -p "$STATE_DIR/states"
    SAFE_NAME="$(printf '%s' "$WORKER_NAME" | tr -c '[:alnum:]_' '_')"
    rm -f "$STATE_DIR/states/${SAFE_NAME}.json" 2>/dev/null || true
    HOOKS_DIR="$GROK_HOME_DIR/hooks"
    HOOKS_FILE="$HOOKS_DIR/darkarchon.json"
    _hook() { printf "'%s' %s" "$STATE_HOOK" "$1"; }
    WANT="{
  \"hooks\": {
    \"SessionStart\":     [{\"hooks\": [{\"type\": \"command\", \"command\": \"$(_hook idle)\", \"timeout\": 5}]}],
    \"UserPromptSubmit\": [{\"hooks\": [{\"type\": \"command\", \"command\": \"$(_hook busy)\", \"timeout\": 5}]}],
    \"Stop\":             [{\"hooks\": [{\"type\": \"command\", \"command\": \"$(_hook stop)\", \"timeout\": 10}]}],
    \"StopCancelled\":    [{\"hooks\": [{\"type\": \"command\", \"command\": \"$(_hook idle)\", \"timeout\": 5}]}],
    \"StopFailure\":      [{\"hooks\": [{\"type\": \"command\", \"command\": \"$(_hook stop-failure)\", \"timeout\": 5}]}],
    \"Notification\":     [{\"hooks\": [{\"type\": \"command\", \"command\": \"$(_hook notification)\", \"timeout\": 5}]}],
    \"PreCompact\":       [{\"hooks\": [{\"type\": \"command\", \"command\": \"$(_hook compacting)\", \"timeout\": 5}]}],
    \"SessionEnd\":       [{\"hooks\": [{\"type\": \"command\", \"command\": \"$(_hook ended)\", \"timeout\": 5}]}]
  }
}"
    mkdir -p "$HOOKS_DIR"
    if [ ! -f "$HOOKS_FILE" ] || [ "$(cat "$HOOKS_FILE")" != "$WANT" ]; then
        printf '%s\n' "$WANT" > "$HOOKS_FILE"
    fi
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
# may not have on PATH. exec keeps our pid so the heartbeat writer's tracking
# stays valid.
GROK_BIN="grok"
if ! command -v grok >/dev/null 2>&1 && [ -x "$HOME/.grok/bin/grok" ]; then
    GROK_BIN="$HOME/.grok/bin/grok"
fi
# shellcheck disable=SC2086
if [ -n "$PROMPT" ]; then
    exec "$GROK_BIN" --rules "$PROMPT" $GROK_ARGS
else
    exec "$GROK_BIN" $GROK_ARGS
fi
