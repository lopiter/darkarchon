#!/usr/bin/env bash
# dispatch-safe.sh — Dispatch only when the worker (and any same-cwd peer) is idle.
#
# All state detection is delegated to lib/worker_state.py (the single resolver:
# hook events > TUI scrape > liveness). This script is the dispatch *policy*:
# which resolved states may receive a task, plus the locking that makes the
# check-then-dispatch atomic against other concurrent dispatchers.
#
# Pre-flight (all via the resolver, no local regex):
#   0. Dependencies from --after still running?       → wait, then refuse if any failed
#   1. Too many consecutive failures for this worker? → refuse (circuit breaker)
#   2. Target busy / compacting / rate_limited?       → refuse (not ready)
#   3. Target awaiting permission / user input?       → refuse (needs the human)
#   4. Codex auth/stream error?                       → refuse (needs `codex login`)
#   5. Unsent user input on the prompt line?          → refuse (unless --force)
#   6. A *different* same-cwd worker busy?            → refuse (serialize edits)
# Checks 1-6 are held under a per-worker lock (and a per-cwd lock when the cwd is
# known) so two dispatchers can't both pass and double-fire. Check 0 runs BEFORE
# the lock is taken: the lock is held for the whole dispatch, so waiting inside it
# for a task on the same worker or cwd would deadlock against the very dispatch
# we are waiting for.
#
# Usage (drop-in replacement for lib/dispatch.sh):
#   dispatch-safe.sh [--force] [--after <id>[,<id>...]] <worker> <prompt...>
#   echo 'long prompt' | dispatch-safe.sh [--force] <worker> -
#
# --force: skip Check 5 AND, for claude workers, blast BSpaces to wipe the prompt
# line before the trigger. Use when the "typed" text is not real user input
# (recap ghost text the dim-heuristic missed). Real user input would be clobbered.
# Also overrides the circuit breaker.
#
# --after: wait for those task ids to complete before dispatching. Ids come from
# earlier dispatches (a task row exists before its trigger is ever sent, so an
# id the caller holds always resolves).
#
# Exit codes:
#   0      success — dispatched and completed (lib/dispatch.sh exit code)
#   10     refused — worker busy / compacting / rate_limited / another dispatch in flight
#   11     refused — unsent user input on the prompt line (suppressed by --force)
#   12     refused — codex worker shows an auth/stream error
#   13     refused — a same-cwd peer worker is busy
#   14     refused — worker awaiting permission or user input
#   15     refused — circuit breaker: repeated failures on this worker (--force overrides)
#   16     refused — a --after dependency failed, was cancelled, or does not exist
#   17     refused — timed out waiting for a --after dependency
#   1/2/3  passthrough from lib/dispatch.sh (bad args / timeout / parse error);
#          also 1 for unknown/dead worker
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/lib/_lib.sh"

FORCE=0
AFTER=""
while [ $# -gt 0 ]; do
    case "$1" in
        --force|--force-clear) FORCE=1; shift ;;
        --after) AFTER="${2:?--after needs a comma-separated task id list}"; shift 2 ;;
        --) shift; break ;;
        -*) echo "ERROR: unknown option '$1'" >&2; exit 1 ;;
        *) break ;;
    esac
done

if [ $# -lt 2 ]; then
    echo "Usage: $0 [--force] [--after <id>[,<id>...]] <worker> <prompt...>" >&2
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

task_status() {
    python3 "$HERE/lib/task_store.py" get "$1" 2>/dev/null \
        | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("status","") if d else "")' 2>/dev/null || true
}

# ─── Check 0: wait for --after dependencies. Runs OUTSIDE the locks. ─────────
# Holding the worker/cwd lock while waiting would deadlock whenever a dependency
# runs on the same worker or the same checkout — we would be waiting for a task
# that cannot start until we release the lock we are holding.
wait_for_deps() {
    [ -z "$AFTER" ] && return 0
    local deadline=$(( $(date +%s) + ${DEPS_WAIT_SEC:-3600} ))
    local dep st
    while IFS=',' read -ra _deps; do
        for dep in "${_deps[@]}"; do
            dep="$(printf '%s' "$dep" | tr -d '[:space:]')"
            [ -z "$dep" ] && continue
            while :; do
                st="$(task_status "$dep")"
                case "$st" in
                    completed) break ;;
                    failed|timeout|cancelled)
                        echo "REFUSED: dependency '$dep' ended as $st — not dispatching." >&2
                        return 16 ;;
                    "")
                        # A task row is written before its trigger is sent, so an
                        # id the caller legitimately holds always resolves. An
                        # empty lookup means a typo or a pruned task; waiting for
                        # it would hang until the deadline for no reason.
                        echo "REFUSED: dependency '$dep' does not exist." >&2
                        return 16 ;;
                esac
                if [ "$(date +%s)" -ge "$deadline" ]; then
                    echo "REFUSED: timed out after ${DEPS_WAIT_SEC:-3600}s waiting for dependency '$dep' (status=$st)." >&2
                    return 17
                fi
                sleep "${DEPS_POLL_INTERVAL:-3}"
            done
        done
    done <<< "$AFTER"
    return 0
}

# ─── The checked dispatch, run INSIDE the lock(s). Returns (never exits) so the
#     lock is always released by with_named_lock's cleanup. ──────────────────
checked_dispatch() {
    local state detail kind fails threshold
    state="$(resolve_field "$WORKER" state)"
    detail="$(resolve_field "$WORKER" detail)"
    kind="$(resolve_field "$WORKER" kind)"

    # ─── Check 1: circuit breaker ───────────────────────────────────────────
    # Consecutive settled failures since this worker's last success. Refusals
    # never write a task row, so a worker that was merely busy doesn't burn the
    # budget — only dispatches that actually reached it and went wrong.
    threshold="${CIRCUIT_THRESHOLD:-3}"
    if [ "$FORCE" -eq 0 ] && [ "$threshold" -gt 0 ]; then
        fails="$(python3 "$HERE/lib/task_store.py" consecutive-failures "$WORKER" 2>/dev/null || echo 0)"
        if [ "${fails:-0}" -ge "$threshold" ]; then
            echo "REFUSED: worker '$WORKER' has failed $fails dispatches in a row (threshold $threshold)." >&2
            echo "  Something is wrong with the worker or the task — check 'lib/tasks.sh failed'." >&2
            echo "  One success resets this; --force dispatches anyway." >&2
            return 15
        fi
    fi

    # ─── Idle debounce ──────────────────────────────────────────────────────
    # A single snapshot can catch the worker between spinner frames, or in the
    # gap between a Stop hook and the background-task notification that
    # re-invokes it. Require IDLE_CONFIRMS consecutive idle resolves (spaced
    # IDLE_CONFIRM_INTERVAL apart) before trusting idle — herdr's working→idle
    # debounce, ported. Any non-idle observation falls through to the normal
    # refusal handling below with the freshly observed state.
    local confirms="${IDLE_CONFIRMS:-3}"
    if [ "$state" = "idle" ] && [ "$confirms" -gt 1 ]; then
        local c
        for c in $(seq 2 "$confirms"); do
            sleep "${IDLE_CONFIRM_INTERVAL:-0.4}"
            state="$(resolve_field "$WORKER" state)"
            if [ "$state" != "idle" ]; then
                detail="$(resolve_field "$WORKER" detail)"
                break
            fi
        done
    fi

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
            # Every other refusal above tells the caller what to do next; this
            # one used to stop at the diagnosis, which left an orchestrator
            # guessing — and guessing here reaches for kill-worker.sh, the one
            # command that destroys a pane a human may be working in.
            if [ "$state" = "dead" ]; then
                if [ "$(resolve_field "$WORKER" orphaned)" = "1" ]; then
                    echo "  This window still hosts an agent that is NOT this worker — someone" >&2
                    echo "  relaunched their own session in it. Do NOT run kill-worker.sh." >&2
                    echo "  Bring the worker back in a fresh window: revive-worker.sh '$WORKER'" >&2
                    echo "  or adopt the pane as-is:                 revive-worker.sh '$WORKER' --adopt" >&2
                else
                    echo "  Restore it with its conversation:  revive-worker.sh '$WORKER'" >&2
                    echo "  Start it over from scratch:        revive-worker.sh '$WORKER' --fresh" >&2
                    echo "  Or drop it from the team:          prune-workers.sh" >&2
                fi
            fi
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

# ─── Check 0: dependencies, before any lock is taken ────────────────────────
wait_for_deps || exit $?

# Record what this task waited on, for anyone reading the row later. Passed by
# env rather than as an argument so it cannot end up in DISPATCH_ARGS and be
# mistaken for part of the prompt.
if [ -n "$AFTER" ]; then
    DISPATCH_DEPS="$(printf '%s' "$AFTER" | tr ',' '\n' | python3 -c 'import json,sys; print(json.dumps([l.strip() for l in sys.stdin if l.strip()]))')"
    export DISPATCH_DEPS
fi

# ─── Acquire lock(s) around the checked dispatch ────────────────────────────
WLOCK="dispatch-$(safe_name "$WORKER")"
if [ -n "$SELF_DIR" ]; then
    # cksum → a short, filesystem-safe key for the cwd (avoids slashes).
    CLOCK="cwd-$(printf '%s' "$SELF_DIR" | cksum | cut -d' ' -f1)"
    take_locks() { with_named_lock "$CLOCK" with_named_lock "$WLOCK" checked_dispatch; }
else
    take_locks() { with_named_lock "$WLOCK" checked_dispatch; }
fi

# with_named_lock gives up after DISPATCH_LOCK_WAIT_SEC (3) and returns 99.
# After --after, we are racing the dependency's own dispatch-safe releasing its
# lock, which happens a moment after the task row goes terminal — so the very
# first attempt can lose by milliseconds. Retry a couple of times before calling
# it contention, but only on the --after path: elsewhere, 99 means someone else
# genuinely holds the worker and the caller should hear that immediately.
rc=0
attempts=1
[ -n "$AFTER" ] && attempts=3
for _ in $(seq 1 "$attempts"); do
    rc=0
    take_locks || rc=$?
    [ "$rc" -ne 99 ] && break
    sleep 1
done

if [ "$rc" -eq 99 ]; then
    echo "REFUSED: another dispatch to '$WORKER' (or its cwd) is already in flight." >&2
    exit 10
fi
exit "$rc"
