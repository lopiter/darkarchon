#!/usr/bin/env bash
# check-worker-state.sh — Report worker state without dispatching.
#
# Outputs a structured one-line state for orchestrators (e.g. dispatcher
# Claude) to decide whether to dispatch new work. Reuses dispatch-safe.sh
# detection logic (active marker regex + dim-escape autocomplete check)
# so the dispatcher and the human see the same classification.
#
# Usage:
#   check-worker-state.sh <worker>             # one-line state
#   check-worker-state.sh <worker> --verbose   # + last 15 pane lines
#
# Exit codes:
#   0  — success (any state)
#   1  — bad args / unknown worker / pane unavailable
#
# Output format (one line, KEY=VALUE space-separated):
#   state=<idle|busy|unsent>
#   worker=<name>
#   target=<session:window>
#   task=<id>           (only if running task tracked in tasks.sh)
#   marker='...'        (only if active marker found in pane)
#   prompt='...'        (only if unsent input or autocomplete ghost text)
#   prompt_kind=<unsent|autocomplete>  (only when prompt has content)
#
# State priority (pane > tasks.sh):
#   busy   — pane shows ✽ Verbing… / "still running" active marker
#   unsent — prompt line has plain (non-dim) text waiting for Enter
#   idle   — empty prompt OR autocomplete ghost text (dim escape codes)
#
# tasks.sh `running` entries are reported as `task=...` for information but do
# NOT determine state — entries can become stale (worker exit / dispatch crash)
# and pane is the real-time source of truth.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/lib/_lib.sh"

VERBOSE=0
WORKER=""
for arg in "$@"; do
    case "$arg" in
        --verbose|-v) VERBOSE=1 ;;
        -h|--help)
            sed -n '2,/^set -euo/p' "$0" | sed 's/^# \?//' | head -n -1
            exit 0
            ;;
        -*) echo "Unknown flag: $arg" >&2; exit 1 ;;
        *) WORKER="$arg" ;;
    esac
done

if [ -z "$WORKER" ]; then
    echo "Usage: $0 <worker> [--verbose]" >&2
    exit 1
fi

TARGET="$(worker_target "$WORKER")"
if [ -z "$TARGET" ]; then
    echo "ERROR: unknown worker '$WORKER'" >&2
    echo "Known: $(all_known_workers | tr '\n' ' ')" >&2
    exit 1
fi
if ! tmux has-session -t "=${TARGET%%:*}" 2>/dev/null; then
    echo "ERROR: tmux session '${TARGET%%:*}' not running" >&2
    exit 1
fi

# ─── tasks.sh: is there a running dispatch for this worker? ────────────
RUNNING_TASK=""
if [ -x "$HERE/lib/tasks.sh" ]; then
    RUNNING_TASK="$(
        "$HERE/lib/tasks.sh" list 2>/dev/null \
            | awk -v w="$WORKER" 'NR>2 && $2 == w && $3 == "running" { print $1; exit }' \
            || true
    )"
fi

# ─── Pane capture ──────────────────────────────────────────────────────
PANE_PLAIN="$(tmux capture-pane -p -t "=$TARGET" 2>/dev/null | grep -v '^[[:space:]]*$' | tail -15 || true)"
PANE_E="$(tmux capture-pane -e -p -t "=$TARGET" -S -8 2>/dev/null | tail -8 || true)"

if [ -z "$PANE_PLAIN" ] && [ -z "$PANE_E" ]; then
    echo "ERROR: failed to capture pane $TARGET" >&2
    exit 1
fi

# ─── Active markers (✽ gerund + ellipsis OR "still running") ──────────
ACTIVE_PATTERN='✽[[:space:]][A-Za-z]+(ing|ling|ning)…|still running'
ACTIVE_MARKER=""
if echo "$PANE_PLAIN" | grep -qE "$ACTIVE_PATTERN"; then
    ACTIVE_MARKER="$(echo "$PANE_PLAIN" | grep -oE "$ACTIVE_PATTERN" | sort -u | tr '\n' ',' | sed 's/,$//')"
fi

# ─── Prompt line analysis (dim escape vs plain) ───────────────────────
PROMPT_LINE="$(echo "$PANE_E" | grep -F $'\xe2\x9d\xaf' | tail -1 || true)"
PROMPT_TEXT=""
PROMPT_KIND=""
if [ -n "$PROMPT_LINE" ]; then
    AFTER_PROMPT="${PROMPT_LINE#*$'\xe2\x9d\xaf'}"
    AFTER_PROMPT="${AFTER_PROMPT# }"
    PLAIN="$(printf '%s' "$AFTER_PROMPT" | sed -E $'s/\x1b\\[[0-9;]*m//g')"
    PLAIN_TRIMMED="$(printf '%s' "$PLAIN" | tr -d '[:space:]')"
    if [ -n "$PLAIN_TRIMMED" ]; then
        PROMPT_TEXT="$PLAIN"
        if printf '%s' "$AFTER_PROMPT" | grep -qE $'\x1b\\[(2|0;2)m'; then
            PROMPT_KIND="autocomplete"
        else
            PROMPT_KIND="unsent"
        fi
    fi
fi

# ─── Determine overall state (pane is authoritative) ─────────────────
if [ -n "$ACTIVE_MARKER" ]; then
    STATE="busy"
elif [ "$PROMPT_KIND" = "unsent" ]; then
    STATE="unsent"
else
    # idle: empty prompt OR autocomplete ghost text
    # (RUNNING_TASK may be stale from a prior crashed dispatch — pane
    # showing no active marker means the worker is ready for new work.
    # task=... is reported as info but does not block state=idle.)
    STATE="idle"
fi

# ─── One-line summary ─────────────────────────────────────────────────
LINE="state=$STATE worker=$WORKER target=$TARGET"
[ -n "$RUNNING_TASK" ] && LINE="$LINE task=$RUNNING_TASK"
[ -n "$ACTIVE_MARKER" ] && LINE="$LINE marker='$ACTIVE_MARKER'"
if [ -n "$PROMPT_TEXT" ]; then
    # Truncate very long prompt for one-liner
    DISPLAY_TEXT="${PROMPT_TEXT:0:80}"
    [ "${#PROMPT_TEXT}" -gt 80 ] && DISPLAY_TEXT="${DISPLAY_TEXT}…"
    LINE="$LINE prompt='$DISPLAY_TEXT' prompt_kind=$PROMPT_KIND"
fi
echo "$LINE"

if [ "$VERBOSE" = "1" ]; then
    echo
    echo "─── Pane (last 15 non-empty lines) ───"
    echo "$PANE_PLAIN"
fi

exit 0
