#!/usr/bin/env bash
# Worker-side launcher for an Antigravity CLI (Google `agy`) team worker.
#
# Counterpart to start-worker-claude.sh / start-worker-grok.sh, using what agy
# offers instead of Claude's flags (all live-verified on agy 1.2.2, 2026-09-12):
#   1. Every workspace agy opens contributes its `.agents/` customizations —
#      `hooks.json` and always-on `rules/*.md` included — and `--add-dir` opens
#      an extra workspace. So the launcher builds a darkarchon-owned workspace
#      under $STATE_DIR/agy/<worker>/ holding the team contract as an always-on
#      rule and the lifecycle hooks, and adds it with `--add-dir`. Nothing lands
#      in the repo or in ~/.gemini/config, and the user's own agy sessions never
#      see any of it.
#   2. Team contract = the same prompt layers as the claude launcher plus
#      prompts/agy.md, which swaps the mcp__darkarchon__* tools for their shell
#      equivalents (lib/ask.sh, lib/mailbox.sh read EE_* themselves — agy hands
#      the launching env to every hook and every shell it runs).
#   3. Hooks: PreInvocation → busy, Stop → idle (+ mailbox gate), received by
#      lib/agy-state-hook.sh. There is no session-start event; the first prompt
#      records the conversation id, which `agy --conversation <id>` resumes.
#   4. Launch with NO positional prompt; we want an empty idle pane that later
#      receives dispatches.
#   5. The startup "Do you trust the contents of this project?" dialog is
#      answered ahead of time by listing the cwd in agy's trustedWorkspaces
#      (AGY_AUTO_TRUST=1, default) — exactly what pressing Enter on it does.
#
# Usage (signature mirrors start-worker-claude.sh for a uniform spawn call site):
#   start-worker-agy.sh <worker_name> <role> <team_root> <state_dir> [<context_dir>] [<resume_conversation_id>]
#
# Env knobs (passed by spawn-worker.sh from config.env):
#   AGY_FLAGS       default: --dangerously-skip-permissions
#                   (agy's "auto-approve all tool permission requests")
#   AGY_MODEL       optional model id (`agy models` lists them); empty = default
#   AGY_AUTO_TRUST  default 1: pre-trust the cwd so the worker never sits on the
#                   trust dialog. 0 = leave the dialog to the human.
#
# Exports for the worker process: EE_WORKER_NAME, EE_TEAM_ROOT, EE_STATE_DIR, EE_ROLE
set -euo pipefail

if [ $# -lt 4 ]; then
    echo "Usage: $0 <worker_name> <role> <team_root> <state_dir> [<context_dir>] [<resume_conversation_id>]" >&2
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

AGY_DATA_DIR="$HOME/.gemini/antigravity-cli"

# Auth pre-flight: agy signs in with Google on first run and keeps its state
# under ~/.gemini/antigravity-cli. Without it the welcome screen asks to sign
# in and every dispatch would sit on that dialog. Warn rather than fail — the
# user may log in after the pane is up.
if [ ! -d "$AGY_DATA_DIR" ] && [ -z "${GEMINI_API_KEY:-}" ]; then
    echo "WARNING: no $AGY_DATA_DIR — agy has never run here and will ask to sign in." >&2
    echo "         Run 'agy' once interactively and authenticate before dispatching." >&2
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
# agy overlay LAST so its tool substitutions override the contract's MCP names.
append_layer "$TEAM_ROOT/prompts/agy.md"
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

# ── The darkarchon workspace agy opens alongside the repo ──────────────────
SAFE_NAME="$(printf '%s' "$WORKER_NAME" | tr -c '[:alnum:]_' '_')"
AGY_WS="$STATE_DIR/agy/$SAFE_NAME"
rm -rf "$AGY_WS"
mkdir -p "$AGY_WS/.agents/rules"

# Contract as an always-on rule. Rules with frontmatter live in .agents/rules/;
# `always_on` loads unconditionally (the alternative, model_decision, would let
# the model skip it).
{
    printf -- '---\ntrigger: always_on\n---\n'
    printf '%s\n' "$PROMPT"
} > "$AGY_WS/.agents/rules/darkarchon-contract.md"

# Lifecycle hooks. hooks.json is keyed by hook NAME; PreInvocation/Stop take a
# flat handler list (only the tool events use the matcher wrapper). Handlers
# run via `sh -c` with cwd = the directory holding hooks.json and inherit this
# process's env — which is how the receiver knows which worker it reports for.
STATE_HOOK="$TEAM_ROOT/lib/agy-state-hook.sh"
if [ -x "$STATE_HOOK" ]; then
    mkdir -p "$STATE_DIR/states"
    rm -f "$STATE_DIR/states/${SAFE_NAME}.json" 2>/dev/null || true
    python3 - "$STATE_HOOK" "$AGY_WS/.agents/hooks.json" <<'PY'
import json, shlex, sys
hook, out = sys.argv[1], sys.argv[2]
def cmd(action, timeout):
    return [{"type": "command", "command": f"{shlex.quote(hook)} {action}", "timeout": timeout}]
cfg = {"darkarchon": {
    "PreInvocation": cmd("busy", 10),
    "Stop":          cmd("stop", 15),
}}
with open(out, "w") as fh:
    json.dump(cfg, fh, indent=2)
PY
fi

# ── Trust the cwd ahead of the dialog ──────────────────────────────────────
# agy keeps trustedWorkspaces in its own settings.json; adding the cwd there is
# what "Yes, I trust this folder" does. Atomic rewrite, other keys untouched.
if [ "${AGY_AUTO_TRUST:-1}" = "1" ]; then
    python3 - "$AGY_DATA_DIR/settings.json" "$(pwd -P)" <<'PY' || true
import json, os, sys
path, cwd = sys.argv[1], sys.argv[2]
try:
    data = json.load(open(path))
    if not isinstance(data, dict):
        data = {}
except Exception:
    data = {}
trusted = data.get("trustedWorkspaces")
if not isinstance(trusted, list):
    trusted = []
if cwd not in trusted:
    trusted.append(cwd)
    data["trustedWorkspaces"] = trusted
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w") as fh:
        json.dump(data, fh, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
PY
fi

# Heartbeat writer tracks our pid. After exec (below) the pid is reused by agy,
# so the writer keeps tracking the live worker and self-exits when it dies.
HEARTBEAT_WRITER="$TEAM_ROOT/lib/heartbeat-writer.sh"
if [ -x "$HEARTBEAT_WRITER" ]; then
    "$HEARTBEAT_WRITER" "$WORKER_NAME" "$STATE_DIR" "$$" &
    disown
fi

# Build agy argv. Never pass a positional prompt.
AGY_FLAGS="${AGY_FLAGS:---dangerously-skip-permissions}"
AGY_MODEL="${AGY_MODEL:-}"
AGY_ARGS="--add-dir $AGY_WS $AGY_FLAGS"
if [ -n "$AGY_MODEL" ]; then
    AGY_ARGS="$AGY_ARGS --model $AGY_MODEL"
fi

# PATH robustness: agy installs to ~/.local/bin, which a non-login tmux pane
# may not have on PATH. exec keeps our pid so the heartbeat writer's tracking
# stays valid.
AGY_BIN="agy"
if ! command -v agy >/dev/null 2>&1 && [ -x "$HOME/.local/bin/agy" ]; then
    AGY_BIN="$HOME/.local/bin/agy"
fi
# shellcheck disable=SC2086
if [ -n "$RESUME_SESSION" ]; then
    exec "$AGY_BIN" --conversation "$RESUME_SESSION" $AGY_ARGS
else
    exec "$AGY_BIN" $AGY_ARGS
fi
