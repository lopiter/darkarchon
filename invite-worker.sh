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

# Optional --kind overrides auto-detection (claude|codex). Default: auto-detect
# from pane content below.
# --force skips the duplicate-target refusal (a pane already registered in any
# team) for the rare case where dual membership is truly intended.
KIND=""
FORCE=0
while [ $# -gt 0 ]; do
    case "$1" in
        --kind)   KIND="${2:-}"; shift 2 ;;
        --kind=*) KIND="${1#--kind=}"; shift ;;
        --force)  FORCE=1; shift ;;
        --)       shift; break ;;
        -*)       echo "ERROR: unknown option '$1'" >&2; exit 1 ;;
        *)        break ;;
    esac
done

if [ $# -lt 2 ]; then
    echo "Usage: $0 [--kind claude|codex] [--force] <name> <session:window> [<role>]" >&2
    echo "  <name>            worker handle (sanitized; matches what dispatch.sh accepts)" >&2
    echo "  <session:window>  tmux target of the existing Claude/codex pane" >&2
    echo "  <role>            free-form label (default: worker-invited)" >&2
    echo "  --kind            force agent flavor; omit to auto-detect from pane" >&2
    echo "  --force           allow a pane that is already registered in another team" >&2
    exit 1
fi
NAME="$1"
TARGET="$2"
ROLE="${3:-worker-invited}"

if [ -n "$KIND" ] && [ "$KIND" != "claude" ] && [ "$KIND" != "codex" ]; then
    echo "ERROR: invalid --kind '$KIND' (expected: claude|codex)" >&2
    exit 1
fi

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

# Refuse a pane that is already registered in ANY team (this one included).
# One pane serving two managers races on dispatch triggers (no cross-team
# lock) and its ask/question queue only ever reaches one of them. Targets are
# canonicalized to session_name:window_index so "sess:1" and "sess:winname"
# registrations of the same pane still match; dead registrations (target no
# longer resolvable) never block. --force bypasses for intentional dual use.
_canon_target() {
    tmux display-message -p -t "=$1" '#{session_name}:#{window_index}' 2>/dev/null || true
}
NEW_CANON="$(_canon_target "$TARGET")"
if [ -n "$NEW_CANON" ] && [ "$FORCE" -eq 0 ]; then
    for REG in "$HOME/.${TOOL_PREFIX:-darkarchon}"/*/workers-runtime.env; do
        [ -f "$REG" ] || continue
        OWNER_TEAM="$(basename "$(dirname "$REG")")"
        while IFS= read -r REG_LINE; do
            case "$REG_LINE" in
                WORKER_*_TARGET=*)
                    OWNER_SN="${REG_LINE#WORKER_}"; OWNER_SN="${OWNER_SN%%_TARGET=*}"
                    VAL="${REG_LINE#*=}"; VAL="${VAL%\'}"; VAL="${VAL#\'}"
                    [ -z "$VAL" ] && continue
                    if [ "$(_canon_target "$VAL")" = "$NEW_CANON" ]; then
                        echo "ERROR: pane '$TARGET' is already registered as worker '$OWNER_SN' in team '$OWNER_TEAM'." >&2
                        echo "One pane must serve one manager — uninvite/kill it there first, or re-run with --force." >&2
                        exit 1
                    fi ;;
            esac
        done < "$REG"
    done
fi

# Derive cwd (best effort)
CWD="$(tmux display-message -p -t "$TARGET" '#{pane_current_path}' 2>/dev/null || true)"
[ -z "$CWD" ] && CWD="(unknown)"

# cwd-collision warning (same rationale as spawn-worker.sh): a busy same-cwd peer
# causes dispatch-safe to refuse, serializing edits to one git working tree.
if [ "$CWD" != "(unknown)" ]; then
    SHARED_CWD="$(workers_sharing_dir "$CWD" "$NAME" | tr '\n' ' ' | sed 's/ *$//')"
    if [ -n "$SHARED_CWD" ]; then
        echo "WARNING: cwd '$CWD' is already used by worker(s): $SHARED_CWD" >&2
        echo "         Dispatches across same-cwd workers are serialized." >&2
    fi
fi

# Heuristic: which agent is in this pane? Check Claude markers first (distinctive
# model names / banner — codex's "esc to interrupt" overlaps Claude's busy line,
# so order matters), then codex markers. An explicit --kind skips detection.
PANE="$(tmux capture-pane -p -t "$TARGET" -S -50 2>/dev/null || true)"
DETECT_NOTE=""
if [ -n "$KIND" ]; then
    DETECT_NOTE="forced via --kind"
elif echo "$PANE" | grep -qE 'Claude Code|bypass permissions on|Opus|Sonnet|Haiku'; then
    KIND=claude; DETECT_NOTE="detected claude"
elif echo "$PANE" | grep -qE 'OpenAI Codex|⌃T transcript|⌃J newline|/approvals|Esc to interrupt'; then
    KIND=codex; DETECT_NOTE="detected codex"
else
    KIND=claude; DETECT_NOTE="unsure-default-claude"
fi

# Lock the window name, exactly as spawn-worker.sh does for panes it creates.
# Without this an invited pane resolves fine at first and then quietly stops:
# tmux's automatic-rename follows pane_current_command, so claude's runtime
# version string ("2.1.220") eventually replaces whatever the window was called
# and the registry's `session:window-name` target no longer matches anything.
tmux set-window-option -t "$TARGET" automatic-rename off >/dev/null 2>&1 || true
tmux set-window-option -t "$TARGET" allow-rename off >/dev/null 2>&1 || true

# tmux's immutable handle for this window — survives renames outright, so the
# resolver has something to match on even if the locks above are undone.
WINDOW_ID="$(tmux display-message -p -t "$TARGET" '#{window_id}' 2>/dev/null || true)"

# Register in runtime — single lock covers both registry append and the
# orchestrator marker write so a concurrent spawn/kill can't interleave.
SAFE="$(safe_name "$NAME")"
_persist_invite_registration() {
    mkdir -p "$STATE_DIR"
    {
        echo ""
        echo "# invited $(date -u +%FT%TZ)  name=$NAME  source=$TARGET  kind=$KIND ($DETECT_NOTE)"
        printf 'WORKER_%s_NAME=%q\n'     "$SAFE" "$NAME"
        printf 'WORKER_%s_TARGET=%q\n'   "$SAFE" "$TARGET"
        if [ -n "$WINDOW_ID" ]; then
            printf 'WORKER_%s_WINDOW_ID=%q\n' "$SAFE" "$WINDOW_ID"
        fi
        printf 'WORKER_%s_DIR=%q\n'      "$SAFE" "$CWD"
        printf 'WORKER_%s_ROLE=%q\n'     "$SAFE" "$ROLE"
        printf 'WORKER_%s_KIND=%q\n'     "$SAFE" "$KIND"
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
echo "  target:  $TARGET (external — not in our session $SESSION_NAME)"
echo "  cwd:     $CWD"
echo "  role:    $ROLE"
echo "  kind:    $KIND ($DETECT_NOTE)"
if [ "$DETECT_NOTE" = "unsure-default-claude" ]; then
    echo
    echo "  WARNING: couldn't detect the agent from pane content — defaulted to claude."
    echo "  If this pane runs codex, re-invite with: $0 --kind codex '$NAME' '$TARGET'"
fi
echo
echo "Dispatch:  $HERE/lib/dispatch.sh $NAME '<prompt>'"
echo "Uninvite:  $HERE/uninvite-worker.sh $NAME"
