#!/usr/bin/env bash
# Worker peer messaging via mailbox files (omc-style write-then-notify).
#
# Each worker has $STATE_DIR/mailboxes/<worker>.jsonl. A sender appends one
# JSON message per line then sends a short tmux trigger to the recipient pane
# so the recipient knows to drain its mailbox. Reading is destructive: drained
# messages are moved to <worker>.drained.jsonl so they aren't seen twice.
#
# Usage:
#   mailbox.sh send <to> <body...>
#       Append a message to <to>'s mailbox + trigger them. <to> is a worker name
#       or a group address (see below).
#   mailbox.sh send <to> --from <from_worker> <body...>
#       Same, but record the sender. Defaults to "external" if --from omitted.
#   mailbox.sh read <worker>
#       Print all undrained messages for <worker> as JSON lines, then move them
#       to the drained sidecar stamped with read_at.
#   mailbox.sh peek <worker>
#       Like read but does NOT drain.
#   mailbox.sh count <worker>
#       Print number of undrained messages.
#   mailbox.sh outstanding <worker> [--older-than <sec>]
#       List undrained messages that have sat unread for <sec> (default
#       $MAILBOX_OUTSTANDING_SEC, 300). These are the ones whose trigger
#       probably never landed.
#   mailbox.sh renotify <worker>
#       Re-send the tmux trigger if anything is still undrained.
#   mailbox.sh clear <worker>
#       Drain everything without printing (forget all queued messages).
#
# Group addresses for `send` (resolved once, at send time, into one message
# record per recipient — each recipient then has its own read tracking):
#   @all           every registered worker
#   @idle          every worker currently reporting idle
#   @claude        every claude worker        @codex   every codex worker
#   @cwd:<dir>     every worker whose cwd is <dir>
# The sender is never included in its own group send. Group names are explicit
# ('@' prefixed) rather than matched against free text, so a worker named after
# an ordinary word can't accidentally collect a broadcast.
#
# Exit codes:
#   0  delivered
#   1  bad args / unknown literal recipient
#   4  group address matched no one (delivering nothing silently is worse than
#      failing — the caller believes it has broadcast)

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/_lib.sh"

MAILBOX_DIR="$STATE_DIR/mailboxes"
mkdir -p "$MAILBOX_DIR"

mailbox_path() { echo "$MAILBOX_DIR/$1.jsonl"; }
drained_path() { echo "$MAILBOX_DIR/$1.drained.jsonl"; }

# notify <to> <from> — best-effort nudge so the recipient drains its mailbox.
# The message is already on disk at this point; the trigger is only a hint, so
# a dead pane or an over-long trigger is not an error. Recovery for a trigger
# that never lands is `outstanding` + `renotify`.
notify() {
    local to="$1" from="$2" target trigger
    target="$(worker_target "$to")"
    [ -z "$target" ] && return 0
    trigger="MAILBOX_NOTIFY from=${from} count_with: ${HERE}/mailbox.sh count ${to}"
    [ "${#trigger}" -gt 199 ] && return 0
    tmux has-session -t "=${target%%:*}" 2>/dev/null || return 0
    # -l --: send the text literally, so a body-derived trigger could never be
    # read as key names. Enter goes separately, as a real key.
    tmux send-keys -t "=$target" -l -- "$trigger" 2>/dev/null || true
    sleep 0.4
    tmux send-keys -t "=$target" Enter 2>/dev/null || true
}

# deliver_one <to> <from> <body> — write then notify. Echoes the message id.
deliver_one() {
    local to="$1" from="$2" body="$3" msg_id created_at
    msg_id="$(date +%s%N)-$(openssl rand -hex 2)"
    created_at="$(date -u +%FT%TZ)"
    jq -n -c \
        --arg id "$msg_id" --arg from "$from" --arg to "$to" \
        --arg body "$body" --arg created_at "$created_at" \
        '{message_id:$id, from_worker:$from, to_worker:$to, body:$body, created_at:$created_at}' \
        >> "$(mailbox_path "$to")"
    notify "$to" "$from"
    echo "$msg_id"
}

# is_group_address <address> — is this a group we know how to resolve?
# Kept separate from expansion because expand_group ends in a pipeline, and a
# `return 1` from inside one is the pipeline's status, not the function's — an
# unknown address would be reported as an empty group instead of a typo.
is_group_address() {
    case "$1" in
        @all|@idle|@claude|@codex|@cwd:*) return 0 ;;
        *) return 1 ;;
    esac
}

# expand_group <address> <sender> — print the recipients an @address resolves to.
# Caller must have validated the address with is_group_address first.
expand_group() {
    local addr="$1" sender="$2" w
    case "$addr" in
        @all)    all_known_workers ;;
        @claude|@codex)
            for w in $(all_known_workers); do
                [ "$(worker_kind "$w")" = "${addr#@}" ] && echo "$w"
            done ;;
        @idle)
            for w in $(all_known_workers); do
                [ "$(python3 "$HERE/worker_state.py" "$w" --field state 2>/dev/null || true)" = "idle" ] \
                    && echo "$w"
            done ;;
        @cwd:*)  workers_sharing_dir "${addr#@cwd:}" ;;
    esac | grep -vx "$sender" || true
}

cmd="${1:-}"
case "$cmd" in
    send)
        to="${2:-}"
        if [ -z "$to" ]; then
            echo "Usage: $0 send <to_worker> [--from <from>] <body...>" >&2
            exit 1
        fi
        shift 2
        from="external"
        if [ "${1:-}" = "--from" ]; then
            from="${2:-external}"
            shift 2
        fi
        if [ "$#" -eq 0 ]; then
            echo "Usage: $0 send <to_worker> [--from <from>] <body...>" >&2
            exit 1
        fi
        body="$*"

        case "$to" in
            @*)
                if ! is_group_address "$to"; then
                    echo "ERROR: unknown group address '$to'" >&2
                    echo "  known: @all @idle @claude @codex @cwd:<dir>" >&2
                    exit 1
                fi
                recipients="$(expand_group "$to" "$from")"
                if [ -z "$recipients" ]; then
                    echo "ERROR: group '$to' matched no recipients — nothing sent." >&2
                    exit 4
                fi
                # One record per recipient so each gets its own read tracking.
                # Labelled output, since a bare id list would not say who got what.
                while IFS= read -r w; do
                    [ -z "$w" ] && continue
                    echo "to=$w id=$(deliver_one "$w" "$from" "$body")"
                done <<< "$recipients"
                ;;
            *)
                if [ -z "$(worker_target "$to")" ]; then
                    echo "ERROR: unknown recipient '$to' (define WORKER_${to}_TARGET or spawn it)" >&2
                    exit 1
                fi
                deliver_one "$to" "$from" "$body"
                ;;
        esac
        ;;

    peek|read)
        who="${2:?Usage: $0 $cmd <worker>}"
        f="$(mailbox_path "$who")"
        if [ ! -f "$f" ]; then
            echo "(empty)"
            exit 0
        fi
        cat "$f"
        if [ "$cmd" = "read" ]; then
            # Stamp read_at as the messages move to the sidecar. Without it there
            # is no record that anyone ever collected them, so a trigger that
            # never landed is indistinguishable from one that did — which is
            # what `outstanding` needs to tell apart. created_at is preserved;
            # the dashboard's recent-activity window keys off it.
            read_at="$(date -u +%FT%TZ)"
            jq -c --arg read_at "$read_at" '. + {read_at:$read_at}' "$f" \
                >> "$(drained_path "$who")"
            : > "$f"
        fi
        ;;

    outstanding)
        who="${2:?Usage: $0 outstanding <worker> [--older-than <sec>]}"
        older="${MAILBOX_OUTSTANDING_SEC:-300}"
        if [ "${3:-}" = "--older-than" ]; then
            older="${4:?--older-than needs a value in seconds}"
        fi
        f="$(mailbox_path "$who")"
        [ ! -s "$f" ] && exit 0
        # Undrained past the grace period ⇒ the recipient never picked it up.
        cutoff="$(python3 -c "import time,sys;print(time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime(time.time()-int(sys.argv[1]))))" "$older")"
        jq -c --arg cutoff "$cutoff" 'select(.created_at <= $cutoff)' "$f"
        ;;

    renotify)
        who="${2:?Usage: $0 renotify <worker>}"
        f="$(mailbox_path "$who")"
        if [ ! -s "$f" ]; then
            echo "nothing outstanding for $who"
            exit 0
        fi
        # Re-fire the trigger for whoever sent the oldest undrained message; the
        # payload is already on disk, this just re-rings the bell.
        from="$(jq -r -s 'if length>0 then .[0].from_worker else "external" end' "$f")"
        notify "$who" "$from"
        echo "renotified $who ($(wc -l < "$f" | tr -d ' ') undrained)"
        ;;

    count)
        who="${2:?Usage: $0 count <worker>}"
        f="$(mailbox_path "$who")"
        if [ ! -s "$f" ]; then
            echo 0
        else
            wc -l < "$f" | tr -d ' '
        fi
        ;;

    clear)
        who="${2:?Usage: $0 clear <worker>}"
        f="$(mailbox_path "$who")"
        if [ -f "$f" ]; then
            cat "$f" >> "$(drained_path "$who")" 2>/dev/null || true
            : > "$f"
        fi
        echo "cleared $who"
        ;;

    *)
        echo "Usage: $0 {send <to> [--from <from>] <body>|read <worker>|peek <worker>|count <worker>|outstanding <worker> [--older-than <sec>]|renotify <worker>|clear <worker>}" >&2
        echo "  <to> may be a worker name or a group: @all @idle @claude @codex @cwd:<dir>" >&2
        exit 1
        ;;
esac
