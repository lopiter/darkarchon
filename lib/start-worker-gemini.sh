#!/usr/bin/env bash
# Worker-side launcher for a Gemini CLI (Google `gemini`) team worker.
#
# Counterpart to start-worker-claude.sh / start-worker-grok.sh, using what
# gemini offers instead of Claude's flags (all live-verified on gemini-cli
# 0.59.0, 2026-09-12):
#   1. Lifecycle hooks come from a per-launch settings file. gemini has no
#      `--settings` flag, but it honours GEMINI_CLI_SYSTEM_SETTINGS_PATH, which
#      replaces the platform-wide "system" settings layer (normally
#      /Library/Application Support/GeminiCli/settings.json — empty on a dev
#      box). We point it at $STATE_DIR/gemini/<worker>-settings.json, merged on
#      top of whatever the real system file holds, so the user's own
#      ~/.gemini/settings.json is never touched and nothing lands in the repo.
#      Hook commands inherit this process's env (EE_* included), which is how
#      lib/gemini-state-hook.sh knows which worker it is reporting for.
#   2. Team contract via the SessionStart hook: gemini has no
#      --append-system-prompt / --rules (GEMINI_SYSTEM_MD would REPLACE the whole
#      system prompt, tool instructions included). SessionStart hooks may return
#      `hookSpecificOutput.additionalContext`, injected as the first turn of the
#      session — so the contract is written to a file here and the hook receiver
#      hands it back on session-start. Same prompt layers as the claude launcher
#      plus prompts/gemini.md, which swaps the mcp__darkarchon__* tools for their
#      shell equivalents (lib/ask.sh, lib/mailbox.sh read EE_* themselves).
#   3. Launch with NO positional prompt (a positional arg starts a turn); we
#      want an empty idle pane that later receives dispatches.
#   4. `--resume <session-id>` restores a previous conversation (gemini accepts
#      the uuid the SessionStart hook recorded), so revive/restore work as for
#      claude workers.
#
# Usage (signature mirrors start-worker-claude.sh for a uniform spawn call site):
#   start-worker-gemini.sh <worker_name> <role> <team_root> <state_dir> [<context_dir>] [<resume_session_id>]
#
# Env knobs (passed by spawn-worker.sh from config.env):
#   GEMINI_FLAGS   default: --approval-mode yolo --skip-trust
#                  (yolo = auto-approve every tool; --skip-trust answers the
#                  "Do you trust the files in this folder?" dialog for this
#                  session so a fresh worker doesn't sit on it)
#   GEMINI_MODEL   optional model id (`-m`); empty = gemini's default
#
# Exports for the worker process: EE_WORKER_NAME, EE_TEAM_ROOT, EE_STATE_DIR,
# EE_ROLE, EE_GEMINI_CONTRACT (path of the contract file the SessionStart hook
# injects).
set -euo pipefail

if [ $# -lt 4 ]; then
    echo "Usage: $0 <worker_name> <role> <team_root> <state_dir> [<context_dir>] [<resume_session_id>]" >&2
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

# Auth pre-flight: gemini keeps a Google login in ~/.gemini/oauth_creds.json,
# or takes an API key from GEMINI_API_KEY / GOOGLE_API_KEY (Vertex via
# GOOGLE_CLOUD_PROJECT). Without any of them the welcome screen asks how to
# authenticate and every dispatch would sit on that dialog. Warn rather than
# fail — the user may log in after the pane is up.
GEMINI_HOME_DIR="$HOME/.gemini"
if [ ! -f "$GEMINI_HOME_DIR/oauth_creds.json" ] && [ -z "${GEMINI_API_KEY:-}" ] \
    && [ -z "${GOOGLE_API_KEY:-}" ] && [ -z "${GOOGLE_CLOUD_PROJECT:-}" ]; then
    echo "WARNING: no $GEMINI_HOME_DIR/oauth_creds.json and no GEMINI_API_KEY/GOOGLE_API_KEY —" >&2
    echo "         gemini will ask how to authenticate. Run 'gemini' once interactively first." >&2
fi

# ── Prompt layers (same stack as start-worker-claude.sh) ───────────────────
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
append_layer "$TEAM_ROOT/prompts/all.md"
[ -n "$CONTEXT_DIR" ] && append_layer "$CONTEXT_DIR/all.md"
ROLE_FILE="$TEAM_ROOT/prompts/${ROLE}.md"
if [ -f "$ROLE_FILE" ]; then
    append_layer "$ROLE_FILE"
else
    append_layer "$TEAM_ROOT/prompts/worker.md"
fi
[ -n "$CONTEXT_DIR" ] && append_layer "$CONTEXT_DIR/${ROLE}.md"
# gemini overlay LAST so its tool substitutions override the contract's MCP names.
append_layer "$TEAM_ROOT/prompts/gemini.md"
# Handover from a previous holder of this name (see leave-team.sh). Consumed
# once. Skipped when resuming: a resumed worker IS the previous holder.
HANDOVER_FILE="$STATE_DIR/handovers/$(printf '%s' "$WORKER_NAME" | tr -c '[:alnum:]_' '_').md"
if [ -z "$RESUME_SESSION" ] && [ -f "$HANDOVER_FILE" ]; then
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

SAFE_NAME="$(printf '%s' "$WORKER_NAME" | tr -c '[:alnum:]_' '_')"
GEMINI_DIR="$STATE_DIR/gemini"
mkdir -p "$GEMINI_DIR"

# The contract file the SessionStart hook injects as additionalContext.
CONTRACT_FILE="$GEMINI_DIR/${SAFE_NAME}-contract.md"
printf '%s\n' "$PROMPT" > "$CONTRACT_FILE"
export EE_GEMINI_CONTRACT="$CONTRACT_FILE"

# ── Lifecycle hooks (per-launch settings file) ─────────────────────────────
# Event → state mapping lives in lib/gemini-state-hook.sh. SessionStart also
# returns the contract; AfterAgent is the turn-end gate that makes the worker
# drain its mailbox before finishing (see the receiver for why).
STATE_HOOK="$TEAM_ROOT/lib/gemini-state-hook.sh"
SETTINGS_FILE=""
if [ -x "$STATE_HOOK" ]; then
    mkdir -p "$STATE_DIR/states"
    rm -f "$STATE_DIR/states/${SAFE_NAME}.json" 2>/dev/null || true
    SETTINGS_FILE="$GEMINI_DIR/${SAFE_NAME}-settings.json"
    # Whatever the platform's real system settings file holds must survive:
    # our env override replaces that layer wholesale, so merge ours on top.
    case "$(uname -s)" in
        Darwin) PLATFORM_SYSTEM_SETTINGS="/Library/Application Support/GeminiCli/settings.json" ;;
        *)      PLATFORM_SYSTEM_SETTINGS="/etc/gemini-cli/settings.json" ;;
    esac
    python3 - "$STATE_HOOK" "$SETTINGS_FILE" "$PLATFORM_SYSTEM_SETTINGS" <<'PY'
import json, shlex, sys
hook, out, base_path = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    base = json.load(open(base_path))
    if not isinstance(base, dict):
        base = {}
except Exception:
    base = {}
def cmd(action, timeout):
    return [{"hooks": [{"type": "command", "name": f"darkarchon-{action}",
                        "command": f"{shlex.quote(hook)} {action}", "timeout": timeout}]}]
hooks = dict(base.get("hooks") or {})
hooks.update({
    "SessionStart": cmd("session-start", 10000),
    "BeforeAgent":  cmd("busy", 5000),
    "AfterAgent":   cmd("after-agent", 10000),
    "Notification": cmd("notification", 5000),
    "PreCompress":  cmd("compacting", 5000),
    "SessionEnd":   cmd("ended", 5000),
})
base["hooks"] = hooks
with open(out, "w") as fh:
    json.dump(base, fh, indent=2)
PY
    export GEMINI_CLI_SYSTEM_SETTINGS_PATH="$SETTINGS_FILE"
fi

# Heartbeat writer tracks our pid. After exec (below) the pid is reused by
# gemini, so the writer keeps tracking the live worker and self-exits when it dies.
HEARTBEAT_WRITER="$TEAM_ROOT/lib/heartbeat-writer.sh"
if [ -x "$HEARTBEAT_WRITER" ]; then
    "$HEARTBEAT_WRITER" "$WORKER_NAME" "$STATE_DIR" "$$" &
    disown
fi

# Build gemini argv. Never pass a positional prompt.
GEMINI_FLAGS="${GEMINI_FLAGS:---approval-mode yolo --skip-trust}"
GEMINI_MODEL="${GEMINI_MODEL:-}"
GEMINI_ARGS="$GEMINI_FLAGS"
if [ -n "$GEMINI_MODEL" ]; then
    GEMINI_ARGS="$GEMINI_ARGS -m $GEMINI_MODEL"
fi

# PATH robustness: the npm global bin (often under ~/.nvm) may be missing from
# a non-login tmux pane's PATH. exec keeps our pid so the heartbeat writer's
# tracking stays valid.
GEMINI_BIN="gemini"
if ! command -v gemini >/dev/null 2>&1; then
    for cand in "$HOME/.npm-global/bin/gemini" "$HOME/.local/bin/gemini" /usr/local/bin/gemini /opt/homebrew/bin/gemini; do
        [ -x "$cand" ] && { GEMINI_BIN="$cand"; break; }
    done
    if [ "$GEMINI_BIN" = "gemini" ] && [ -d "$HOME/.nvm/versions/node" ]; then
        nvm_cand="$(ls -1 "$HOME"/.nvm/versions/node/*/bin/gemini 2>/dev/null | sort -V | tail -1 || true)"
        [ -n "$nvm_cand" ] && [ -x "$nvm_cand" ] && GEMINI_BIN="$nvm_cand"
    fi
fi
# shellcheck disable=SC2086
if [ -n "$RESUME_SESSION" ]; then
    exec "$GEMINI_BIN" --resume "$RESUME_SESSION" $GEMINI_ARGS
else
    exec "$GEMINI_BIN" $GEMINI_ARGS
fi
