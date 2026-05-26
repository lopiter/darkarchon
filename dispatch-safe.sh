#!/usr/bin/env bash
# dispatch-safe.sh — Dispatch only when worker pane is idle.
#
# Pre-flight check: capture pane, look for busy/thinking markers from
# Claude Code TUI. If busy (whether processing a previous dispatch OR
# handling user direct input), refuse with a status report. If idle,
# delegate to lib/dispatch.sh transparently.
#
# Usage (drop-in replacement for lib/dispatch.sh):
#   dispatch-safe.sh <worker> <prompt...>
#   echo 'long prompt' | dispatch-safe.sh <worker> -
#
# Exit codes:
#   0    success — dispatched and completed (lib/dispatch.sh exit code)
#   10   refused — worker busy (won't dispatch, user must wait or interrupt)
#   11   refused — pane content shows possible user-typed input pending
#   1/2/3  passthrough from lib/dispatch.sh (bad args / timeout / parse error)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/lib/_lib.sh"

if [ $# -lt 2 ]; then
    echo "Usage: $0 <worker> <prompt...>" >&2
    echo "       echo 'long prompt' | $0 <worker> -" >&2
    exit 1
fi

WORKER="$1"

# Resolve target
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

# Capture the visible viewport (full pane content). Use whole pane rather than
# `tail -N` because fresh workers have blank lines at bottom that would strip
# to empty under bash command substitution.
PANE_TAIL="$(tmux capture-pane -p -t "=$TARGET" 2>/dev/null || true)"
if [ -z "$PANE_TAIL" ]; then
    echo "ERROR: failed to capture pane $TARGET (worker not running?)" >&2
    exit 1
fi
# Trim trailing blank lines and keep only the meaningful bottom portion (last 15 lines)
PANE_TAIL="$(printf '%s\n' "$PANE_TAIL" | grep -v '^[[:space:]]*$' | tail -15)"

# Active-state markers (only present DURING current work):
#   - ✽ + gerund + ellipsis   → e.g. "✽ Whisking…", "✽ Sautéing…"
#                                (✻ alone with past tense like "✻ Cooked for 32s"
#                                 is a COMPLETED indicator, not active)
#   - "still running"          → background commands actively executing
# Note: "thinking" in OMC status line is sticky/unreliable — not used.
ACTIVE_PATTERN='✽[[:space:]][A-Za-z]+(ing|ling|ning)…|still running'

if echo "$PANE_TAIL" | grep -qE "$ACTIVE_PATTERN"; then
    matched=$(echo "$PANE_TAIL" | grep -oE "$ACTIVE_PATTERN" | sort -u | tr '\n' ',' | sed 's/,$//')
    echo "REFUSED: worker '$WORKER' ($TARGET) appears actively processing." >&2
    echo "  matched markers: $matched" >&2
    echo "  recent pane (last 5 lines):" >&2
    echo "$PANE_TAIL" | tail -5 | sed 's/^/    /' >&2
    echo >&2
    echo "  Wait for it to finish, OR if user is directly chatting with this worker," >&2
    echo "  ask them to detach first. Then retry." >&2
    exit 10
fi

# ─── Check 2: typed-but-unsent user input on prompt line ────────────────
# Capture WITH escape codes (-e) so we can distinguish placeholder/autocomplete
# (rendered with \x1b[7m reverse + \x1b[2m dim) from real user typing (plain).
PANE_E="$(tmux capture-pane -e -p -t "=$TARGET" -S -8 2>/dev/null | tail -8 || true)"
# Find prompt line (contains ❯ / U+276F)
PROMPT_LINE="$(echo "$PANE_E" | grep -F $'\xe2\x9d\xaf' | tail -1 || true)"

if [ -n "$PROMPT_LINE" ]; then
    # Strip everything up to and including "❯ " (prompt + one space)
    AFTER_PROMPT="${PROMPT_LINE#*$'\xe2\x9d\xaf'}"
    AFTER_PROMPT="${AFTER_PROMPT# }"
    # Plain content (no escape codes)
    PLAIN="$(printf '%s' "$AFTER_PROMPT" | sed -E $'s/\x1b\\[[0-9;]*m//g')"
    # Trim whitespace
    PLAIN_TRIMMED="$(printf '%s' "$PLAIN" | tr -d '[:space:]')"

    if [ -n "$PLAIN_TRIMMED" ]; then
        # Content present. Distinguish placeholder vs user input:
        #   - placeholder/autocomplete: rendered with \x1b[2m (dim) — e.g. "Try '...'" or
        #     a recalled previous command shown in faint text
        #   - user typed text: NO dim styling. Cursor highlight (\x1b[7m reverse) may exist
        #     at end of line but isn't dim — so checking only [2m is the reliable signal.
        if printf '%s' "$AFTER_PROMPT" | grep -qE $'\x1b\\[(2|0;2)m'; then
            # Dim styling present — placeholder/autocomplete → treat as idle
            :
        else
            echo "REFUSED: worker '$WORKER' ($TARGET) prompt line has unsent user input." >&2
            echo "  prompt content: $PLAIN" >&2
            echo >&2
            echo "  사용자가 워커에 직접 입력 중인 것으로 보입니다. Enter 로 보내거나 지운 뒤 다시 시도해주세요." >&2
            exit 11
        fi
    fi
fi

# Idle — delegate to real dispatch.sh
exec "$HERE/lib/dispatch.sh" "$@"
