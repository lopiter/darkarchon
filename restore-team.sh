#!/usr/bin/env bash
# restore-team.sh — Bring a whole team back after a reboot.
#
# A reboot (or a killed tmux server) takes every worker's pane with it but
# leaves the team's state dir intact: the registry still names each worker
# with its cwd, role and kind, and the state hook has each claude worker's
# session id. revive-worker.sh already turns those back into a running worker,
# one name at a time. This runs it for every dead worker in the team, so the
# team comes back with one command instead of one per worker.
#
# Usage:
#   restore-team.sh                    # revive every dead worker in this team
#   restore-team.sh --dry-run          # print the plan, change nothing
#   restore-team.sh --fresh            # respawn without conversations (see below)
#   restore-team.sh --only <name>...   # restrict to the named worker(s)
#
# Per worker, the plan is:
#   resume   dead, and a claude session id is recorded -> claude --resume
#   fresh    dead, no session recorded (invited worker, codex, pre-hook) -> clean
#            spawn; a handover note under handovers/ is picked up if present
#   skip     not dead -> left exactly as it is
#
# --fresh forces the second mode for every worker. Resuming restores context
# exactly as it was, which is right after a reboot and wrong for a worker that
# was killed BECAUSE its context was full; for a team of those, use --fresh.
#
# Live workers are never touched and no tmux window is ever killed — a revived
# worker gets a NEW window, and any window that still exists under its name is
# renamed aside (revive-worker.sh does that part).
#
# Exit codes:
#   0  every dead worker revived, or nothing needed reviving
#   1  bad args / unknown --only name
#   3  at least one worker failed to revive (the others were still attempted)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/lib/_lib.sh"

DRY_RUN=0
FRESH=0
ONLY=()
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run|-n) DRY_RUN=1; shift ;;
        --fresh)      FRESH=1; shift ;;
        --only)       [ -n "${2:-}" ] || { echo "ERROR: --only needs a worker name" >&2; exit 1; }
                      ONLY+=("$2"); shift 2 ;;
        --only=*)     ONLY+=("${1#--only=}"); shift ;;
        -h|--help)
            sed -n '2,/^set -euo/p' "$0" | sed 's/^# \?//' | head -n -1
            exit 0 ;;
        *) echo "ERROR: unknown argument '$1'" >&2; exit 1 ;;
    esac
done

KNOWN=()
while IFS= read -r w; do
    [ -n "$w" ] && KNOWN+=("$w")
done < <(all_known_workers)

if [ "${#ONLY[@]}" -gt 0 ]; then
    for o in "${ONLY[@]}"; do
        found=0
        for k in "${KNOWN[@]:-}"; do [ "$k" = "$o" ] && found=1 && break; done
        if [ "$found" -eq 0 ]; then
            echo "ERROR: no worker '$o' in team '$SESSION_NAME'" >&2
            echo "Known workers: ${KNOWN[*]:-(none)}" >&2
            exit 1
        fi
    done
    CANDIDATES=("${ONLY[@]}")
else
    CANDIDATES=("${KNOWN[@]:-}")
fi

# recorded_session <name> -> the claude session id the state hook last wrote
# for this worker, or the one its recall record kept; empty if neither exists.
recorded_session() {
    local safe; safe="$(safe_name "$1")"
    local src
    for src in "$STATE_DIR/states/$safe.json" "$(worker_tombstone_path "$1")"; do
        [ -f "$src" ] || continue
        local id
        id="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("session_id", "") or "")' "$src" 2>/dev/null || true)"
        if [ -n "$id" ]; then echo "$id"; return; fi
    done
}

# ── Plan ────────────────────────────────────────────────────────────────────
PLAN_NAME=(); PLAN_MODE=(); PLAN_NOTE=()
for w in "${CANDIDATES[@]}"; do
    [ -n "$w" ] || continue
    STATE="$(python3 "$HERE/lib/worker_state.py" "$w" --field state 2>/dev/null || true)"
    TARGET="$(worker_target "$w")"
    KIND="$(worker_kind "$w")"; [ -z "$KIND" ] && KIND=claude
    if [ -n "$STATE" ] && [ "$STATE" != "dead" ] && [ "$STATE" != "unknown" ]; then
        PLAN_NAME+=("$w"); PLAN_MODE+=(skip); PLAN_NOTE+=("$STATE, left alone")
        continue
    fi
    if [ "$FRESH" -eq 1 ]; then
        PLAN_NAME+=("$w"); PLAN_MODE+=(fresh); PLAN_NOTE+=("--fresh")
    elif [ "$KIND" != "claude" ]; then
        PLAN_NAME+=("$w"); PLAN_MODE+=(fresh); PLAN_NOTE+=("$KIND cannot resume a conversation")
    else
        SID="$(recorded_session "$w")"
        if [ -n "$SID" ]; then
            PLAN_NAME+=("$w"); PLAN_MODE+=(resume); PLAN_NOTE+=("$SID")
        elif worker_is_external "$w"; then
            PLAN_NAME+=("$w"); PLAN_MODE+=(fresh); PLAN_NOTE+=("invited worker, no session recorded — becomes a spawned one")
        else
            PLAN_NAME+=("$w"); PLAN_MODE+=(fresh); PLAN_NOTE+=("no session recorded")
        fi
    fi
done

TODO=0
for m in "${PLAN_MODE[@]:-}"; do [ "$m" = "resume" ] || [ "$m" = "fresh" ] && TODO=$((TODO + 1)); done
if [ "$TODO" -eq 0 ]; then
    if [ "${#PLAN_NAME[@]}" -eq 0 ]; then
        echo "Team '$SESSION_NAME' has no registered workers — nothing to restore."
    else
        echo "Every worker in team '$SESSION_NAME' is alive — nothing to restore."
    fi
    exit 0
fi

echo "Restore plan for team '$SESSION_NAME' ($TODO to revive):"
for i in "${!PLAN_NAME[@]}"; do
    w="${PLAN_NAME[$i]}"; m="${PLAN_MODE[$i]}"; n="${PLAN_NOTE[$i]}"
    case "$m" in
        resume) printf '  %-24s resume %s\n' "$w" "$n" ;;
        fresh)  printf '  %-24s fresh  (%s)\n' "$w" "$n" ;;
        skip)   printf '  %-24s skip   (%s)\n' "$w" "$n" ;;
    esac
done
echo

if [ "$DRY_RUN" -eq 1 ]; then
    echo "--dry-run: nothing changed."
    exit 0
fi

# ── Revive, one at a time, never stopping at a failure ──────────────────────
REVIVED=(); FAILED=()
for i in "${!PLAN_NAME[@]}"; do
    w="${PLAN_NAME[$i]}"; m="${PLAN_MODE[$i]}"
    [ "$m" = "skip" ] && continue
    ARGS=("$w")
    [ "$m" = "fresh" ] && ARGS+=(--fresh)
    echo "── $w"
    if "$HERE/revive-worker.sh" "${ARGS[@]}"; then
        REVIVED+=("$w")
    else
        echo "FAILED: $w (revive-worker.sh exit $?)" >&2
        FAILED+=("$w")
    fi
    echo
done

echo "Restored ${#REVIVED[@]}/$TODO worker(s) in team '$SESSION_NAME'."
[ "${#REVIVED[@]}" -gt 0 ] && echo "  revived: ${REVIVED[*]}"
if [ "${#FAILED[@]}" -gt 0 ]; then
    echo "  failed:  ${FAILED[*]}   (re-run with --only <name> after fixing the cause)"
fi
echo "Give each new pane ~15s to start; a trust prompt in any window wants an Enter."
[ "${#FAILED[@]}" -eq 0 ] || exit 3
