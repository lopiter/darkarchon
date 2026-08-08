#!/usr/bin/env bash
# revive-worker.sh — Bring a dead or departed worker back, with its previous
# Claude conversation restored.
#
# The parts for this already existed and were never joined up: the state hook
# records each worker's Claude session id, spawn-worker.sh can relaunch with
# `claude --resume <id>`, and a departing worker leaves a tombstone holding its
# cwd/role/kind. What was missing is the one command that puts them together —
# so reviving a worker meant hand-editing workers-runtime.env.
#
# Usage:
#   revive-worker.sh <name>                    # respawn in a NEW window, resuming its conversation
#   revive-worker.sh <name> --fresh            # respawn with no conversation (see below)
#   revive-worker.sh <name> --session-id <id>  # resume a specific session instead of the recorded one
#   revive-worker.sh <name> --adopt            # register the agent already running in its old pane
#   revive-worker.sh <name> --dry-run          # print the plan, change nothing
#
# --fresh vs the default: `--resume` restores the conversation as it was, which
# is right after a reboot or an accidental kill, and wrong when the worker was
# killed BECAUSE its context filled up — resuming replays it straight back into
# the wall. For that case use --fresh, which picks up the worker's handover note
# (lib/leave-team.sh) instead of its transcript.
#
# --adopt does not respawn: when someone has already relaunched their own agent
# in the worker's old window, this hands that pane back to the team rather than
# starting a second one. The adopted session keeps whatever context it has, but
# it runs without the team charter, heartbeat and hooks a spawned worker gets,
# so its state can only be read by scraping the pane.
#
# Exit codes:
#   0  revived
#   1  bad args / unknown worker / nothing to resume
#   2  refused: the worker is alive (kill it first if you really want a restart)
#   3  refused: --adopt but no agent is running in that pane
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/lib/_lib.sh"

NAME=""
RESUME_ID=""
FRESH=0
ADOPT=0
DRY_RUN=0
while [ $# -gt 0 ]; do
    case "$1" in
        --session-id)   RESUME_ID="${2:-}"; shift 2 ;;
        --session-id=*) RESUME_ID="${1#--session-id=}"; shift ;;
        --fresh)        FRESH=1; shift ;;
        --adopt)        ADOPT=1; shift ;;
        --dry-run|-n)   DRY_RUN=1; shift ;;
        -h|--help)
            sed -n '2,/^set -euo/p' "$0" | sed 's/^# \?//' | head -n -1
            exit 0 ;;
        -*) echo "ERROR: unknown option '$1'" >&2; exit 1 ;;
        *)  NAME="$1"; shift ;;
    esac
done

if [ -z "$NAME" ]; then
    echo "Usage: $0 <name> [--fresh|--adopt] [--session-id <id>] [--dry-run]" >&2
    exit 1
fi
if [ "$FRESH" -eq 1 ] && [ -n "$RESUME_ID" ]; then
    echo "ERROR: --fresh and --session-id are contradictory (one skips the" >&2
    echo "       conversation, the other names which one to restore)" >&2
    exit 1
fi
if [ "$ADOPT" -eq 1 ] && { [ "$FRESH" -eq 1 ] || [ -n "$RESUME_ID" ]; }; then
    echo "ERROR: --adopt takes over a running agent, so there is nothing to resume." >&2
    exit 1
fi

SAFE="$(safe_name "$NAME")"
TOMB="$(worker_tombstone_path "$NAME")"

# ── Where does this worker's identity come from? ────────────────────────────
# Still registered (the usual case: it died but nobody cleaned up) → read the
# registry. Already deregistered/pruned/left → read the tombstone it left.
REGISTERED=0
if [ -n "$(worker_target "$NAME")" ]; then
    REGISTERED=1
    TARGET="$(worker_target "$NAME")"
    CWD="$(worker_dir "$NAME")"
    ROLE="$(worker_role "$NAME")"
    KIND="$(worker_kind "$NAME")"
    WIN_SESSION="$(worker_session "$NAME")"
    WINDOW_ID="$(worker_window_id "$NAME")"
    WAS_EXTERNAL=0
    worker_is_external "$NAME" && WAS_EXTERNAL=1
elif [ -f "$TOMB" ]; then
    read_tomb() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get(sys.argv[2], "") or "")' "$TOMB" "$1" 2>/dev/null || true; }
    TARGET="$(read_tomb target)"
    CWD="$(read_tomb cwd)"
    ROLE="$(read_tomb role)"
    KIND="$(read_tomb kind)"
    WIN_SESSION="$(read_tomb session)"
    WINDOW_ID="$(read_tomb window_id)"
    WAS_EXTERNAL=0
    [ "$(read_tomb external)" = "True" ] && WAS_EXTERNAL=1
    LEFT_AT="$(read_tomb departed_at)"
    [ -n "$LEFT_AT" ] && LEFT_AT="$(date -r "$LEFT_AT" '+%F %T' 2>/dev/null || echo "$LEFT_AT")"
    echo "Reviving from recall record ($(read_tomb reason)${LEFT_AT:+, left $LEFT_AT})" >&2
else
    echo "ERROR: no worker '$NAME' — not registered, and no recall record at" >&2
    echo "       $TOMB" >&2
    echo "Known workers: $(all_known_workers | tr '\n' ' ')" >&2
    echo "For a worker that never belonged to this team, use spawn-worker.sh." >&2
    exit 1
fi
[ -z "$KIND" ] && KIND=claude
[ -z "$ROLE" ] && ROLE=worker
[ -z "$WIN_SESSION" ] && WIN_SESSION="${TARGET%%:*}"

if [ -z "$CWD" ] || [ ! -d "$CWD" ]; then
    echo "ERROR: recorded cwd for '$NAME' is missing or gone: '${CWD:-(none)}'" >&2
    echo "       Respawn it explicitly: lib/spawn-worker.sh '$NAME' <cwd> '$ROLE'" >&2
    exit 1
fi

# ── Refuse to disturb a worker that is actually working ─────────────────────
if [ "$REGISTERED" -eq 1 ]; then
    STATE="$(python3 "$HERE/lib/worker_state.py" "$NAME" --field state 2>/dev/null || true)"
    if [ "$STATE" != "dead" ] && [ "$STATE" != "unknown" ] && [ -n "$STATE" ]; then
        echo "REFUSED: worker '$NAME' ($TARGET) is $STATE, not dead." >&2
        echo "  Reviving a live worker would start a second one on the same checkout." >&2
        echo "  End it first with lib/kill-worker.sh if you really want a restart." >&2
        exit 2
    fi
fi

# Is an agent still sitting in the old window? Decides whether that window can
# be reused, and it is the thing --adopt takes over.
OLD_WINDOW_EXISTS=0
OLD_WINDOW_PROC=""
WIN_REF=""
# Prefer the immutable window id, but only when it still lives in the session we
# expect: tmux reissues ids from @0 after a server restart, so a stale id can
# point at a stranger's window — and this script renames what it finds.
if [ -n "$WINDOW_ID" ]; then
    FOUND_SESSION="$(tmux display-message -p -t "$WINDOW_ID" '#{session_name}' 2>/dev/null || true)"
    [ -n "$FOUND_SESSION" ] && [ "$FOUND_SESSION" = "$WIN_SESSION" ] && WIN_REF="$WINDOW_ID"
fi
if [ -z "$WIN_REF" ] && [ -n "$TARGET" ] \
        && tmux display-message -p -t "=$TARGET" '#{window_id}' >/dev/null 2>&1; then
    WIN_REF="=$TARGET"
fi
if [ -n "$WIN_REF" ]; then
    OLD_WINDOW_EXISTS=1
    OLD_WINDOW_PROC="$(tmux display-message -p -t "$WIN_REF" '#{pane_current_command}' 2>/dev/null || true)"
fi
OCCUPIED=0
if [ -n "$OLD_WINDOW_PROC" ] && python3 -c '
import sys
sys.path.insert(0, sys.argv[2])
from lib.tmux_scanner import looks_like_agent_process
sys.exit(0 if looks_like_agent_process(sys.argv[1]) else 1)' "$OLD_WINDOW_PROC" "$HERE" 2>/dev/null; then
    OCCUPIED=1
fi

# ── --adopt: hand the pane that is already running back to the team ─────────
if [ "$ADOPT" -eq 1 ]; then
    if [ "$OCCUPIED" -eq 0 ]; then
        echo "REFUSED: nothing to adopt — no agent is running in '$TARGET'" >&2
        echo "  (foreground command: '${OLD_WINDOW_PROC:-none}')." >&2
        echo "  Respawn it instead: $0 '$NAME'" >&2
        exit 3
    fi
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "DRY RUN — would adopt the running agent in $TARGET as worker '$NAME'"
        [ "$REGISTERED" -eq 1 ] && echo "  1. deregister the dead '$NAME' (pane untouched)"
        echo "  2. invite-worker.sh '$NAME' '$TARGET' '$ROLE' --kind $KIND"
        exit 0
    fi
    if [ "$REGISTERED" -eq 1 ]; then
        DEREGISTER_REASON="adopted" "$HERE/lib/deregister-worker.sh" "$NAME" --force --quiet
    fi
    "$HERE/invite-worker.sh" --kind "$KIND" "$NAME" "$TARGET" "$ROLE"
    echo
    echo "Adopted the session already running in $TARGET as '$NAME'."
    echo "  It runs WITHOUT the team charter, hooks and heartbeat a spawned worker"
    echo "  gets — its state is scrape-only, and it has not read the team contract."
    echo "  Brief it yourself, or replace it later with: $0 '$NAME'"
    exit 0
fi

# ── Which conversation to restore ───────────────────────────────────────────
STATE_FILE="$STATE_DIR/states/$SAFE.json"
RECORDED_ID=""
for src in "$STATE_FILE" "$TOMB"; do
    [ -n "$RECORDED_ID" ] && break
    [ -f "$src" ] || continue
    RECORDED_ID="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("session_id", "") or "")' "$src" 2>/dev/null || true)"
done

if [ "$FRESH" -eq 0 ] && [ -z "$RESUME_ID" ]; then
    RESUME_ID="$RECORDED_ID"
fi
if [ "$KIND" != "claude" ] && [ -n "$RESUME_ID" ]; then
    echo "NOTE: '$NAME' is a $KIND worker — only claude can resume a conversation." >&2
    echo "      Respawning fresh." >&2
    RESUME_ID=""
fi
if [ "$FRESH" -eq 0 ] && [ -z "$RESUME_ID" ] && [ "$KIND" = "claude" ]; then
    echo "ERROR: no Claude session recorded for '$NAME' — nothing to resume." >&2
    echo "  It predates the state hook, was an invited (external) worker, or its" >&2
    echo "  state file was cleared. Start it over with: $0 '$NAME' --fresh" >&2
    echo "  or name the session yourself: $0 '$NAME' --session-id <id>" >&2
    exit 1
fi
if [ -n "$RESUME_ID" ] && [[ ! "$RESUME_ID" =~ ^[a-zA-Z0-9-]+$ ]]; then
    echo "ERROR: invalid session id '$RESUME_ID'" >&2
    exit 1
fi

# ── Plan ────────────────────────────────────────────────────────────────────
# The old window is never killed. If it still exists it is renamed aside, both
# because it may hold work someone cares about and because tmux would otherwise
# have two windows named '$NAME' in one session — making the registry's
# `session:window-name` target ambiguous.
STASH_NAME=""
if [ "$OLD_WINDOW_EXISTS" -eq 1 ]; then
    STASH_NAME="${NAME}-old"
    n=2
    while tmux list-windows -t "=$WIN_SESSION" -F '#W' 2>/dev/null | grep -qx "$STASH_NAME"; do
        STASH_NAME="${NAME}-old$n"; n=$((n + 1))
    done
fi

if [ "$DRY_RUN" -eq 1 ]; then
    echo "DRY RUN — revive '$NAME'"
    echo "  cwd:     $CWD"
    echo "  role:    $ROLE   kind: $KIND   session: $WIN_SESSION"
    [ "$REGISTERED" -eq 1 ] && echo "  1. deregister the dead registration ($TARGET), pane untouched"
    if [ -n "$STASH_NAME" ]; then
        echo "  2. rename old window $TARGET -> $STASH_NAME (occupied: $([ "$OCCUPIED" -eq 1 ] && echo yes || echo no))"
    fi
    if [ -n "$RESUME_ID" ]; then
        echo "  3. spawn-worker.sh --resume-session $RESUME_ID '$NAME' '$CWD' '$ROLE'"
    else
        echo "  3. spawn-worker.sh '$NAME' '$CWD' '$ROLE'   (fresh — no conversation)"
    fi
    exit 0
fi

if [ "$REGISTERED" -eq 1 ]; then
    DEREGISTER_REASON="revived" "$HERE/lib/deregister-worker.sh" "$NAME" --force --quiet
fi

if [ -n "$STASH_NAME" ]; then
    tmux rename-window -t "$WIN_REF" "$STASH_NAME" 2>/dev/null || true
    tmux set-window-option -t "$WIN_REF" automatic-rename off >/dev/null 2>&1 || true
    if [ "$OCCUPIED" -eq 1 ]; then
        echo "Old window kept as '$WIN_SESSION:$STASH_NAME' — an agent is still running in it."
    else
        echo "Old window kept as '$WIN_SESSION:$STASH_NAME' (idle shell; close it when you like)."
    fi
fi

SPAWN_ARGS=(--kind "$KIND")
if [ "$WIN_SESSION" != "$SESSION_NAME" ]; then
    SPAWN_ARGS+=(--session "$WIN_SESSION")
fi
[ -n "$RESUME_ID" ] && SPAWN_ARGS+=(--resume-session "$RESUME_ID")
SPAWN_ARGS+=("$NAME" "$CWD" "$ROLE")

"$HERE/lib/spawn-worker.sh" "${SPAWN_ARGS[@]}"

echo
if [ -n "$RESUME_ID" ]; then
    echo "Revived '$NAME' with its previous conversation (claude --resume $RESUME_ID)."
    echo "  If it was killed because its context was full, that context comes back"
    echo "  too — use --fresh instead to start clean from its handover note."
else
    echo "Revived '$NAME' fresh (no conversation restored)."
    HANDOVER="$STATE_DIR/handovers/$SAFE.md"
    if [ -f "$HANDOVER" ]; then
        echo "  Its handover note was picked up at launch: $HANDOVER"
    fi
fi
if [ "$WAS_EXTERNAL" -eq 1 ]; then
    echo "  NOTE: '$NAME' used to be an invited (external) worker; it is now a"
    echo "        spawned one, with the team charter, hooks and heartbeat attached."
fi
