#!/usr/bin/env bash
# Worker-side launcher: layer generic team contract + project-specific context
# into Claude --append-system-prompt, then exec claude.
#
# Called from spawn-worker.sh via tmux send-keys. Multi-line content with
# arbitrary characters (including double quotes) is handled safely here
# because $(<file) expansion happens inside this script's process, not
# in the send-keys string.
#
# Usage:
#   start-worker-claude.sh <worker_name> <role> <team_root> <state_dir> [<context_dir>] [<resume_session_id>]
#
# A non-empty <resume_session_id> relaunches with `claude --resume <id>` so
# the worker keeps its previous conversation (spawn-worker --resume-session).
#
# Layering order (later concatenations override earlier ones in the model's mind):
#   1. <team_root>/prompts/all.md           — generic team contract (project-agnostic)
#   2. <context_dir>/all.md                 — project-specific team context (optional)
#   3. <team_root>/prompts/<role>.md        — generic role contract
#      (or <team_root>/prompts/worker.md as fallback)
#   4. <context_dir>/<role>.md              — project-specific role context (optional)
#   5. <state_dir>/handovers/<worker>.md    — the previous holder of this worker
#      name's parting note (lib/leave-team.sh). Read once, then archived.
#      Skipped when resuming: a resumed worker wrote it.
#   6. Runtime context tail (worker name, cwd, ...)
#
# Files that are missing are silently skipped — every layer is best-effort.
#
# Exports for the worker process:
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
CONTEXT_DIR="${5:-}"
RESUME_SESSION="${6:-}"

export EE_WORKER_NAME="$WORKER_NAME"
export EE_TEAM_ROOT="$TEAM_ROOT"
export EE_STATE_DIR="$STATE_DIR"
export EE_ROLE="$ROLE"

# append_layer <file> — append file contents to $PROMPT separated by ---,
# silently skipping missing files.
PROMPT=""
append_layer() {
    local f="$1"
    if [ -n "$f" ] && [ -f "$f" ]; then
        if [ -z "$PROMPT" ]; then
            PROMPT="$(<"$f")"
        else
            PROMPT="${PROMPT}

---

$(<"$f")"
        fi
    fi
}

# Layer 1: generic team contract
append_layer "$TEAM_ROOT/prompts/all.md"

# Layer 2: project-specific team context (optional)
if [ -n "$CONTEXT_DIR" ]; then
    append_layer "$CONTEXT_DIR/all.md"
fi

# Layer 3: generic role contract (specific role file or worker.md fallback)
ROLE_FILE="$TEAM_ROOT/prompts/${ROLE}.md"
if [ -f "$ROLE_FILE" ]; then
    append_layer "$ROLE_FILE"
else
    append_layer "$TEAM_ROOT/prompts/worker.md"
fi

# Layer 4: project-specific role context (optional)
if [ -n "$CONTEXT_DIR" ]; then
    append_layer "$CONTEXT_DIR/${ROLE}.md"
fi

# Layer 5: handover from the previous holder of this worker name, if one
# resigned with lib/leave-team.sh. Consumed exactly once — it is moved aside
# after being read, so a worker that outlives its predecessor's note doesn't
# keep being told about work that finished days ago.
#
# Skipped when resuming: a resumed worker IS the previous holder and already
# lived through everything the note describes.
HANDOVER_FILE="$STATE_DIR/handovers/$(printf '%s' "$WORKER_NAME" | tr -c '[:alnum:]_' '_').md"
if [ -z "$RESUME_SESSION" ] && [ -f "$HANDOVER_FILE" ]; then
    append_layer "$HANDOVER_FILE"
    mv "$HANDOVER_FILE" "${HANDOVER_FILE%.md}.consumed.md" 2>/dev/null || true
fi

# Layer 6: runtime context tail
PROMPT="${PROMPT}

---

## Runtime Context (this worker)

- EE_WORKER_NAME=${WORKER_NAME}
- EE_ROLE=${ROLE}
- EE_TEAM_ROOT=${TEAM_ROOT}
- EE_STATE_DIR=${STATE_DIR}
- cwd=$(pwd)
"

# CLAUDE_FLAGS may be set in env (from config.env via spawn-worker), default sane
CLAUDE_FLAGS="${CLAUDE_FLAGS:---permission-mode auto}"

# Spawn the heartbeat writer in the background — it tracks our pid, which
# after `exec claude` is the claude process itself. The writer self-exits
# (and removes its heartbeat file) when our pid goes away.
HEARTBEAT_WRITER="$TEAM_ROOT/lib/heartbeat-writer.sh"
if [ -x "$HEARTBEAT_WRITER" ]; then
    "$HEARTBEAT_WRITER" "$WORKER_NAME" "$STATE_DIR" "$$" &
    disown
fi

# Generate a per-worker MCP config so Claude Code launches our stdio
# server with the right worker identity (env). Skipped silently when
# the server file isn't present (legacy install).
MCP_CONFIG=""
MCP_SERVER="$TEAM_ROOT/lib/mcp_server.py"
if [ -f "$MCP_SERVER" ]; then
    MCP_CONFIG="$STATE_DIR/mcp-config-${WORKER_NAME}.json"
    mkdir -p "$STATE_DIR"
    cat > "$MCP_CONFIG" <<EOF
{
  "mcpServers": {
    "darkarchon": {
      "command": "python3",
      "args": ["$MCP_SERVER"],
      "env": {
        "EE_WORKER_NAME": "$WORKER_NAME",
        "EE_STATE_DIR": "$STATE_DIR"
      }
    }
  }
}
EOF
    CLAUDE_FLAGS="$CLAUDE_FLAGS --mcp-config $MCP_CONFIG"
fi

# Generate a per-worker hooks settings file and inject it via --settings so this
# worker reports its state through Claude Code hook events (event-driven, no TUI
# scraping). The file lives in $STATE_DIR — OUTSIDE the worker's repo — so it
# merges with the repo's own .claude/ settings and leaves no trace to clean up.
# Skipped silently if the receiver script is absent (legacy install → the worker
# falls back to scrape-based detection, same as invited/codex workers).
STATE_HOOK="$TEAM_ROOT/lib/state-hook.sh"
if [ -x "$STATE_HOOK" ]; then
    mkdir -p "$STATE_DIR/states"
    # Fresh slate: drop any stale state file from a prior worker of this name so
    # worker_state.py never trusts a leftover 'busy' before the first event.
    SAFE_NAME="$(printf '%s' "$WORKER_NAME" | tr -c '[:alnum:]_' '_')"
    rm -f "$STATE_DIR/states/${SAFE_NAME}.json" 2>/dev/null || true

    HOOKS_CONFIG="$STATE_DIR/hooks-settings-${WORKER_NAME}.json"
    _hook() { printf "'%s' '%s' %s" "$STATE_HOOK" "$WORKER_NAME" "$1"; }
    cat > "$HOOKS_CONFIG" <<EOF
{
  "hooks": {
    "SessionStart":      [{"hooks": [{"type": "command", "command": "$(_hook idle)"}]}],
    "UserPromptSubmit":  [{"hooks": [{"type": "command", "command": "$(_hook busy)"}]}],
    "Stop":              [{"hooks": [{"type": "command", "command": "$(_hook idle)"}]}],
    "PermissionRequest": [{"matcher": "*", "hooks": [{"type": "command", "command": "$(_hook awaiting_permission)"}]}],
    "Notification":      [{"hooks": [{"type": "command", "command": "$(_hook awaiting_user)"}]}],
    "PreCompact":        [{"hooks": [{"type": "command", "command": "$(_hook compacting)"}]}],
    "SessionEnd":        [{"hooks": [{"type": "command", "command": "$(_hook ended)"}]}]
  }
}
EOF
    CLAUDE_FLAGS="$CLAUDE_FLAGS --settings $HOOKS_CONFIG"
fi

# Exec into Claude. If user has a different binary in PATH, this still works.
# --resume must precede the flag soup so a stray flag value can't swallow it.
# shellcheck disable=SC2086
if [ -n "$RESUME_SESSION" ]; then
    if [ -z "$PROMPT" ]; then
        exec claude --resume "$RESUME_SESSION" $CLAUDE_FLAGS
    else
        exec claude --resume "$RESUME_SESSION" --append-system-prompt "$PROMPT" $CLAUDE_FLAGS
    fi
elif [ -z "$PROMPT" ]; then
    exec claude $CLAUDE_FLAGS
else
    exec claude --append-system-prompt "$PROMPT" $CLAUDE_FLAGS
fi
