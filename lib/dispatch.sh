#!/usr/bin/env bash
# Dispatch a task to a worker using file-based prompt + outbox (omc-style).
#
# Flow:
#   1. Write full prompt to $STATE_DIR/io/prompt-<id>.txt
#   2. Send a SHORT (<200 char) trigger via tmux send-keys telling worker to
#      Read the prompt file, work, and Write the answer to result-<id>.txt
#   3. Poll the worker pane only for the literal "DONE-<id>" marker
#   4. When marker appears, read the result file (no pane parsing)
#   5. Persist task state to $STATE_DIR/tasks/<id>.json
#
# Usage:
#   dispatch.sh <worker> <prompt...>
#   dispatch.sh homepage "분석할 내용..."
#   echo "긴 multi-line 프롬프트..." | dispatch.sh website -
#
# Exit codes:
#   0  success — answer printed to stdout, JSON status=completed
#   1  unknown worker / bad args / target not found
#   2  timeout — JSON status=timeout
#   3  DONE marker observed but result file missing/empty (parse error)

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/_lib.sh"

if [ $# -lt 2 ]; then
    echo "Usage: $0 <worker> <prompt...>" >&2
    echo "       echo 'long prompt' | $0 <worker> -" >&2
    echo >&2
    echo "Known workers: $(all_known_workers | tr '\n' ' ')" >&2
    exit 1
fi

WORKER="$1"
shift
if [ "$#" -eq 1 ] && [ "$1" = "-" ]; then
    PROMPT="$(cat)"
else
    PROMPT="$*"
fi

# Resolve tmux target from worker name
TARGET="$(worker_target "$WORKER")"
if [ -z "$TARGET" ]; then
    echo "ERROR: unknown worker '$WORKER'. Define WORKER_${WORKER}_TARGET in config.env or spawn it first." >&2
    echo "Known workers: $(all_known_workers | tr '\n' ' ')" >&2
    exit 1
fi

# Verify session exists
if ! tmux has-session -t "=${TARGET%%:*}" 2>/dev/null; then
    echo "ERROR: tmux session '${TARGET%%:*}' is not running." >&2
    exit 1
fi
# Verify pane exists (session:window or session:index)
WIN="${TARGET#*:}"
if ! tmux list-windows -t "=${TARGET%%:*}" -F '#W' 2>/dev/null | grep -qx "$WIN" \
    && ! tmux list-windows -t "=${TARGET%%:*}" -F '#I' 2>/dev/null | grep -qx "$WIN"; then
    echo "ERROR: tmux target '$TARGET' not found." >&2
    exit 1
fi

# Generate IDs
TASK_ID="$(date +%Y%m%d-%H%M%S)-$(openssl rand -hex 2)"
DONE_MARKER="DONE-${TASK_ID}"

# Filesystem layout
# IO files use the short /tmp/ee path so the trigger fits in tmux's 200-char budget.
# Persistent task state stays under $STATE_DIR.
IO_DIR="/tmp/ee"
TASKS_DIR="$STATE_DIR/tasks"
mkdir -p "$IO_DIR" "$TASKS_DIR"
PROMPT_FILE="$IO_DIR/p-${TASK_ID}.txt"
RESULT_FILE="$IO_DIR/r-${TASK_ID}.txt"
TASK_FILE="$TASKS_DIR/$TASK_ID.json"

DISPATCHED_AT="$(date -u +%FT%TZ)"

# Determine where this dispatch came from. When called from inside a tmux
# pane (e.g. via a Claude Code Bash invocation), $TMUX_PANE is inherited.
# Resolve it to "session:window" so the dashboard can render the call edge.
DISPATCHED_BY=""
if [ -n "${TMUX_PANE:-}" ]; then
    # Include pane_index so split panes within the same window can be
    # distinguished as separate orchestrators (otherwise all panes of a
    # window share one sender_key and get treated as a single source).
    DISPATCHED_BY="$(tmux display-message -p -t "$TMUX_PANE" '#{session_name}:#{window_index}.#{pane_index}' 2>/dev/null || true)"
fi

# Write the full prompt to disk so the worker reads it via Read tool — bypasses
# tmux send-keys length / wrapping / sentinel rendering issues.
printf '%s\n' "$PROMPT" > "$PROMPT_FILE"

# Initial pending JSON
jq -n \
    --arg id "$TASK_ID" \
    --arg worker "$WORKER" \
    --arg target "$TARGET" \
    --arg prompt_file "$PROMPT_FILE" \
    --arg result_file "$RESULT_FILE" \
    --arg done "$DONE_MARKER" \
    --arg prompt "$PROMPT" \
    --arg dispatched_at "$DISPATCHED_AT" \
    --arg dispatched_by "$DISPATCHED_BY" \
    '{
        id: $id,
        worker: $worker,
        tmux_target: $target,
        prompt_file: $prompt_file,
        result_file: $result_file,
        done_marker: $done,
        prompt: $prompt,
        status: "pending",
        dispatched_at: $dispatched_at,
        dispatched_by: $dispatched_by,
        completed_at: null,
        result: null,
        error: null
    }' > "$TASK_FILE"

# Mirror initial pending row into the SQLite store. Best-effort: failure must
# not break dispatch (json file remains the source-of-truth for backward compat
# until the migration is complete).
python3 "$HERE/task_store.py" insert "$TASK_FILE" 2>/dev/null || true

update_status() {
    local stat="$1"
    local tmp="$TASK_FILE.tmp.$$"
    jq --arg s "$stat" '.status = $s' "$TASK_FILE" > "$tmp"
    mv "$tmp" "$TASK_FILE"
    python3 "$HERE/task_store.py" update-status "$TASK_ID" "$stat" 2>/dev/null || true
}

set_result() {
    local result="$1"
    local completed_at="$(date -u +%FT%TZ)"
    local tmp="$TASK_FILE.tmp.$$"
    jq --arg r "$result" --arg t "$completed_at" \
        '. + {status: "completed", completed_at: $t, result: $r}' "$TASK_FILE" > "$tmp"
    mv "$tmp" "$TASK_FILE"
    python3 "$HERE/task_store.py" update-status "$TASK_ID" completed --result "$result" 2>/dev/null || true
}

set_error() {
    local errmsg="$1"
    local stat="$2"
    local completed_at="$(date -u +%FT%TZ)"
    local tmp="$TASK_FILE.tmp.$$"
    jq --arg e "$errmsg" --arg s "$stat" --arg t "$completed_at" \
        '. + {status: $s, completed_at: $t, error: $e}' "$TASK_FILE" > "$tmp"
    mv "$tmp" "$TASK_FILE"
    python3 "$HERE/task_store.py" update-status "$TASK_ID" "$stat" --error "$errmsg" 2>/dev/null || true
}

# Build the short trigger — kept under 200 chars for safety.
# Worker reads the prompt file, does the work, writes answer file, prints DONE marker.
TRIGGER="Read ${PROMPT_FILE} then Write final answer to ${RESULT_FILE} then output ${DONE_MARKER}"

if [ "${#TRIGGER}" -gt 199 ]; then
    echo "ERROR: trigger length ${#TRIGGER} exceeds 199 chars (paths too long)." >&2
    set_error "trigger length exceeded" failed
    exit 1
fi

# Send via tmux send-keys (single short line, no embedded newlines)
tmux send-keys -t "=$TARGET" "$TRIGGER"
sleep 0.6
tmux send-keys -t "=$TARGET" Enter

update_status running

# Poll pane only for DONE marker presence (no content extraction from pane)
START_EPOCH=$(date +%s)
while true; do
    NOW=$(date +%s)
    ELAPSED=$((NOW - START_EPOCH))
    if [ "$ELAPSED" -gt "${TASK_TIMEOUT:-300}" ]; then
        set_error "timeout after ${TASK_TIMEOUT}s waiting for ${DONE_MARKER}" timeout
        echo "TIMEOUT: $TASK_ID (target=$TARGET)" >&2
        exit 2
    fi

    # Just look for DONE marker in pane. We need it to appear at least twice
    # (echo of trigger + worker's actual output) to know the worker actually
    # produced it, not just our trigger echo.
    PANE="$(tmux capture-pane -p -J -t "=$TARGET" -S -2000 2>/dev/null || true)"
    occurrences=$(grep -cF "$DONE_MARKER" <<<"$PANE" || true)
    if [ "${occurrences:-0}" -ge 2 ]; then
        # Worker has printed the marker. Give the filesystem a brief moment to
        # ensure the Write tool finished flushing.
        sleep 1
        if [ ! -s "$RESULT_FILE" ]; then
            # File missing or empty even though marker shown — sometimes Write
            # comes microseconds after; retry once.
            sleep 2
        fi
        if [ ! -s "$RESULT_FILE" ]; then
            set_error "DONE marker observed but result file missing or empty" failed
            echo "PARSE_ERROR: $TASK_ID (no result at $RESULT_FILE)" >&2
            exit 3
        fi
        RESULT="$(cat "$RESULT_FILE")"
        # Strip trailing whitespace/newlines for clean output
        RESULT="${RESULT%"${RESULT##*[![:space:]]}"}"
        set_result "$RESULT"
        printf '%s\n' "$RESULT"
        exit 0
    fi
    sleep "${POLL_INTERVAL:-3}"
done
