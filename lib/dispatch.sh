#!/usr/bin/env bash
# Dispatch a task to a worker using file-based prompt + outbox (omc-style).
#
# Flow:
#   1. Write full prompt to /tmp/ee/p-<id>.txt
#   2. Send a SHORT (<200 char) trigger via tmux send-keys telling worker to
#      Read the prompt file, work, and Write the answer to r-<id>.txt
#   3. Poll the worker pane only for the literal "DONE-<id>" marker
#   4. When marker appears, read the result file (no pane parsing)
#   5. Persist task state in $STATE_DIR/tasks.db via lib/task_store.py
#
# Usage:
#   dispatch.sh <worker> <prompt...>
#   dispatch.sh homepage "what to analyze..."
#   echo "long multi-line prompt..." | dispatch.sh website -
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

# Agent flavor drives send-keys style + completion detection below.
KIND="$(worker_kind "$WORKER")"

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
# IO files use a short, per-user path (/tmp/<prefix>-<uid>, mode 700) so the
# trigger fits in tmux's 200-char budget AND other users on a shared host can't
# read a worker's prompts (the old world-readable /tmp/ee leaked them). Persistent
# task state lives in $STATE_DIR/tasks.db (managed by task_store.py); the plain
# files here are transient scratch, safe to lose on reboot.
IO_DIR="$(io_dir)"
PROMPT_FILE="$IO_DIR/p-${TASK_ID}.txt"
RESULT_FILE="$IO_DIR/r-${TASK_ID}.txt"

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

# Insert the initial pending row directly into SQLite (stdin pipe — no json file).
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
        dispatched_by: $dispatched_by
    }' | python3 "$HERE/task_store.py" insert

update_status() {
    local stat="$1"
    python3 "$HERE/task_store.py" update-status "$TASK_ID" "$stat"
}

set_result() {
    local result="$1"
    python3 "$HERE/task_store.py" update-status "$TASK_ID" completed --result "$result"
}

set_error() {
    local errmsg="$1"
    local stat="$2"
    python3 "$HERE/task_store.py" update-status "$TASK_ID" "$stat" --error "$errmsg"
}

# Build the short trigger — kept under 200 chars for safety.
# Worker reads the prompt file, does the work, writes answer file, prints DONE marker.
TRIGGER="Read ${PROMPT_FILE} then Write final answer to ${RESULT_FILE} then output ${DONE_MARKER}"

if [ "${#TRIGGER}" -gt 199 ]; then
    echo "ERROR: trigger length ${#TRIGGER} exceeds 199 chars (paths too long)." >&2
    set_error "trigger length exceeded" failed
    exit 1
fi

# send_trigger — deliver the short "go" line to the worker. Kind-specific only
# in HOW keys are sent; the payload is identical. Called once up front and once
# more as a nudge if the worker ends its turn without producing a result.
send_trigger() {
    if [ "$KIND" = "codex" ]; then
        # codex needs literal mode: `-l` stops tmux interpreting words like "Enter"
        # as key names, `--` guards a message starting with `-`. codex's TUI also
        # occasionally swallows the first Enter, so we double-press; an empty second
        # submit is a harmless no-op on the codex composer.
        tmux send-keys -t "=$TARGET" -l -- "$TRIGGER"
        sleep 0.2
        tmux send-keys -t "=$TARGET" Enter
        sleep 0.2
        tmux send-keys -t "=$TARGET" Enter
    else
        tmux send-keys -t "=$TARGET" "$TRIGGER"
        sleep 0.6
        tmux send-keys -t "=$TARGET" Enter
    fi
}

# finalize_success — read the result file (authoritative completion signal for
# BOTH kinds now: the worker Writes it as its last step) and exit 0. The DONE
# marker is no longer required — pane marker-counting was fragile under codex's
# alt-screen and Claude's scrollback wrapping.
finalize_success() {
    sleep 1  # let the Write flush fully before reading
    if [ ! -s "$RESULT_FILE" ]; then sleep 2; fi   # Write can land microseconds late
    if [ ! -s "$RESULT_FILE" ]; then
        set_error "completion signal but result file missing or empty" failed
        echo "PARSE_ERROR: $TASK_ID (no result at $RESULT_FILE)" >&2
        exit 3
    fi
    RESULT="$(cat "$RESULT_FILE")"
    RESULT="${RESULT%"${RESULT##*[![:space:]]}"}"   # strip trailing whitespace
    set_result "$RESULT"
    printf '%s\n' "$RESULT"
    exit 0
}

send_trigger
update_status running

# ── Poll for completion ─────────────────────────────────────────────────────
# Completion is the RESULT FILE, not a wall-clock deadline. Two stop conditions
# besides success:
#   - hard cap TASK_MAX_SECONDS (default 3600) — backstop for a wedged worker.
#     Long-running tasks no longer die at the old flat 300s.
#   - activity-based turn-end: if the worker returns to idle (its turn ended)
#     without writing a result, it ignored the contract. We nudge once (re-send
#     the trigger) and, if it idles again with no result, fail fast — far quicker
#     than waiting out the cap. Idle is confirmed over N polls so a single
#     mis-scrape (scrape-based workers) doesn't trip it; hook-based workers report
#     idle authoritatively but still pass through the same debounce harmlessly.
START_EPOCH=$(date +%s)
MAX="${TASK_MAX_SECONDS:-3600}"
IDLE_CONFIRM="${TASK_IDLE_CONFIRM:-3}"       # confirmed-idle polls ⇒ turn ended
NOSTART_CONFIRM="${TASK_NOSTART_CONFIRM:-10}" # idle-from-the-start polls ⇒ trigger never took
seen_busy=0
idle_streak=0
nudged=0

while true; do
    # 1. Authoritative: result file present ⇒ done.
    if [ -s "$RESULT_FILE" ]; then
        finalize_success
    fi

    NOW=$(date +%s)
    if [ "$((NOW - START_EPOCH))" -gt "$MAX" ]; then
        set_error "hard cap ${MAX}s reached without result" timeout
        echo "TIMEOUT: $TASK_ID (target=$TARGET, cap=${MAX}s)" >&2
        exit 2
    fi

    # 2. Worker activity via the shared resolver (hook events > scrape).
    STATE="$(python3 "$HERE/worker_state.py" "$WORKER" --field state 2>/dev/null || true)"
    case "$STATE" in
        busy|compacting) seen_busy=1; idle_streak=0 ;;
        idle)            idle_streak=$((idle_streak + 1)) ;;
        dead)
            set_error "worker died mid-task (state=dead)" failed
            echo "WORKER_DEAD: $TASK_ID (target=$TARGET)" >&2
            exit 3 ;;
        *)               idle_streak=0 ;;  # awaiting_user/rate_limited/error/unknown: occupied or blocked — wait under the cap
    esac

    turn_end=0
    if [ "$seen_busy" -eq 1 ] && [ "$idle_streak" -ge "$IDLE_CONFIRM" ]; then
        turn_end=1
    elif [ "$seen_busy" -eq 0 ] && [ "$idle_streak" -ge "$NOSTART_CONFIRM" ]; then
        turn_end=1  # never observed busy ⇒ the trigger likely didn't take
    fi

    if [ "$turn_end" -eq 1 ] && [ ! -s "$RESULT_FILE" ]; then
        if [ "$nudged" -eq 0 ]; then
            echo "NUDGE: $TASK_ID idle without result — re-sending trigger once" >&2
            send_trigger
            nudged=1; seen_busy=0; idle_streak=0
        else
            set_error "worker returned to idle without writing result (after 1 nudge)" failed
            echo "NO_RESULT: $TASK_ID (target=$TARGET)" >&2
            exit 3
        fi
    fi

    sleep "${POLL_INTERVAL:-3}"
done
