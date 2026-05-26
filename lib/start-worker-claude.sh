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
#   start-worker-claude.sh <worker_name> <role> <team_root> <state_dir> [<context_dir>]
#
# Layering order (later concatenations override earlier ones in the model's mind):
#   1. <team_root>/prompts/all.md           — generic team contract (project-agnostic)
#   2. <context_dir>/all.md                 — project-specific team context (optional)
#   3. <team_root>/prompts/<role>.md        — generic role contract
#      (or <team_root>/prompts/worker.md as fallback)
#   4. <context_dir>/<role>.md              — project-specific role context (optional)
#   5. Runtime context tail (worker name, cwd, ...)
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

# Layer 5: runtime context tail
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

# Exec into Claude. If user has a different binary in PATH, this still works.
# shellcheck disable=SC2086
if [ -z "$PROMPT" ]; then
    exec claude $CLAUDE_FLAGS
else
    exec claude --append-system-prompt "$PROMPT" $CLAUDE_FLAGS
fi
