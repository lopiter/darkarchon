#!/usr/bin/env bash
# dispatch-safe.sh — Dispatch only when the worker (and any same-cwd peer) is idle.
#
# All state detection is delegated to lib/worker_state.py (the single resolver:
# hook events > TUI scrape > liveness). This script is the dispatch *policy*:
# which resolved states may receive a task, plus the locking that makes the
# check-then-dispatch atomic against other concurrent dispatchers.
#
# Pre-flight (all via the resolver, no local regex):
#   1. Target busy / compacting / rate_limited?      → refuse (not ready)
#   2. Target awaiting_user (permission prompt etc.)? → refuse (needs the human)
#   3. Codex auth/stream error?                       → refuse (needs `codex login`)
#   4. Unsent user input on the prompt line?          → refuse (unless --force)
#   5. A *different* same-cwd worker busy?            → refuse (serialize edits)
# Held under a per-worker lock (and a per-cwd lock when the cwd is known) so two
# dispatchers can't both pass the checks and double-fire.
#
# Usage (drop-in replacement for lib/dispatch.sh):
#   dispatch-safe.sh [--force] <worker> <prompt...>
#   echo 'long prompt' | dispatch-safe.sh [--force] <worker> -
#
# --force: skip Check 4 AND, for claude workers, blast BSpaces to wipe the prompt
# line before the trigger. Use when the "typed" text is not real user input
# (recap ghost text the dim-heuristic missed). Real user input would be clobbered.
#
# Exit codes:
#   0      success — dispatched and completed (lib/dispatch.sh exit code)
#   10     refused — worker busy / compacting / rate_limited / another dispatch in flight
#   11     refused — unsent user input on the prompt line (suppressed by --force)
#   12     refused — codex worker shows an auth/stream error
#   13     refused — a same-cwd peer worker is busy
#   14     refused — worker awaiting user input (permission prompt / question)
#   1/2/3  passthrough from lib/dispatch.sh (bad args / timeout / parse error);
#          also 1 for unknown/dead worker
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/lib/_lib.sh"

FORCE=0
while [ $# -gt 0 ]; do
    case "$1" in
        --force|--force-clear) FORCE=1; shift ;;
        --) shift; break ;;
        -*) echo "ERROR: unknown option '$1'" >&2; exit 1 ;;
        *) break ;;
    esac
done

if [ $# -lt 2 ]; then
    echo "Usage: $0 [--force] <worker> <prompt...>" >&2
    echo "       echo 'long prompt' | $0 [--force] <worker> -" >&2
    exit 1
fi

DISPATCH_ARGS=("$@")
WORKER="$1"

# Resolve target + cwd from the registry (cheap, no tmux).
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
SELF_DIR="$(worker_dir "$WORKER")"

# resolve_field <worker> <field> — one field from the resolver (state|detail|kind).
resolve_field() {
    python3 "$HERE/lib/worker_state.py" "$1" --field "$2" 2>/dev/null || true
}

# ─── The checked dispatch, run INSIDE the lock(s). Returns (never exits) so the
#     lock is always released by with_named_lock's cleanup. ──────────────────
checked_dispatch() {
    local state detail kind
    state="$(resolve_field "$WORKER" state)"
    detail="$(resolve_field "$WORKER" detail)"
    kind="$(resolve_field "$WORKER" kind)"

    case "$state" in
        busy|compacting|rate_limited)
            echo "REFUSED: worker '$WORKER' ($TARGET) is $state." >&2
            [ -n "$detail" ] && echo "  detail: $detail" >&2
            echo "  Wait for it to finish, or if you're chatting with it directly, detach first." >&2
            return 10 ;;
        error)
            echo "REFUSED: codex worker '$WORKER' ($TARGET) shows an auth/stream error." >&2
            [ -n "$detail" ] && echo "  detail: $detail" >&2
            echo "  The codex token appears expired or not logged in. Run 'codex login' and try again." >&2
            return 12 ;;
        awaiting_permission)
            echo "REFUSED: worker '$WORKER' ($TARGET) is blocked on a permission prompt." >&2
            [ -n "$detail" ] && echo "  detail: $detail" >&2
            echo "  Approve or deny it in the worker's pane, then try again." >&2
            return 14 ;;
        awaiting_user)
            echo "REFUSED: worker '$WORKER' ($TARGET) is awaiting user input." >&2
            [ -n "$detail" ] && echo "  detail: $detail" >&2
            echo "  The worker asked a question and is waiting on an answer. Respond first, then try again." >&2
            return 14 ;;
        dead|unknown)
            echo "REFUSED: worker '$WORKER' ($TARGET) is $state." >&2
            [ -n "$detail" ] && echo "  detail: $detail" >&2
            return 1 ;;
        unsent)
            if [ "$FORCE" -eq 0 ]; then
                echo "REFUSED: worker '$WORKER' ($TARGET) has unsent user input on the prompt line." >&2
                [ -n "$detail" ] && echo "  prompt content: $detail" >&2
                echo "  It looks like you're typing directly. Send it with Enter or clear the line, then try again." >&2
                return 11
            fi
            ;;  # --force: fall through, pre-clear happens below
        idle)
            : ;;  # ready
        *)
            echo "WARNING: unrecognized resolver state '$state' for '$WORKER' — proceeding." >&2 ;;
    esac

    # ─── Check 5: same-cwd peer serialization ───────────────────────────────
    if [ -n "$SELF_DIR" ]; then
        local peer peer_state
        while IFS= read -r peer; do
            [ -z "$peer" ] && continue
            peer_state="$(resolve_field "$peer" state)"
            if [ "$peer_state" = "busy" ] || [ "$peer_state" = "compacting" ]; then
                echo "REFUSED: peer worker '$peer' shares cwd '$SELF_DIR' and is $peer_state." >&2
                echo "  Dispatches are serialized to avoid git working-tree conflicts — try again once '$peer' finishes." >&2
                return 13
            fi
        done < <(workers_sharing_dir "$SELF_DIR" "$WORKER")
    fi

    # ─── --force pre-clear (claude only): wipe ghost text before the trigger ─
    if [ "$FORCE" -eq 1 ] && [ "$kind" != "codex" ]; then
        tmux send-keys -t "=$TARGET" C-u C-k 2>/dev/null || true
        local bsp=""
        local i
        for i in $(seq 1 200); do bsp="$bsp BSpace"; done
        # shellcheck disable=SC2086
        tmux send-keys -t "=$TARGET" $bsp 2>/dev/null || true
        sleep 0.4
    fi

    # Ready — run the real dispatch as a child (lock stays held for its duration
    # so a second dispatch to this worker serializes behind it).
    "$HERE/lib/dispatch.sh" "${DISPATCH_ARGS[@]}"
}

# ─── Acquire lock(s) around the checked dispatch ────────────────────────────
WLOCK="dispatch-$(safe_name "$WORKER")"
rc=0
if [ -n "$SELF_DIR" ]; then
    # cksum → a short, filesystem-safe key for the cwd (avoids slashes).
    CLOCK="cwd-$(printf '%s' "$SELF_DIR" | cksum | cut -d' ' -f1)"
    with_named_lock "$CLOCK" with_named_lock "$WLOCK" checked_dispatch || rc=$?
else
    with_named_lock "$WLOCK" checked_dispatch || rc=$?
fi

if [ "$rc" -eq 99 ]; then
    echo "REFUSED: another dispatch to '$WORKER' (or its cwd) is already in flight." >&2
    exit 10
fi
exit "$rc"
