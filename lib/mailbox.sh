#!/usr/bin/env bash
# Worker peer messaging via mailbox files (omc-style write-then-notify).
#
# Each worker has $STATE_DIR/mailboxes/<worker>.jsonl. A sender appends one
# JSON message per line then sends a short tmux trigger to the recipient pane
# so the recipient knows to drain its mailbox. Reading is destructive: drained
# messages are moved to <worker>.drained.jsonl so they aren't seen twice.
#
# Usage:
#   mailbox.sh send <to_worker> <body...>
#       Append a message to <to_worker>'s mailbox + trigger them.
#   mailbox.sh send <to_worker> --from <from_worker> <body...>
#       Same, but record the sender. Defaults to "external" if --from omitted.
#   mailbox.sh read <worker>
#       Print all undrained messages for <worker> as JSON lines, then move them
#       to the drained sidecar.
#   mailbox.sh peek <worker>
#       Like read but does NOT drain.
#   mailbox.sh count <worker>
#       Print number of undrained messages.
#   mailbox.sh clear <worker>
#       Drain everything without printing (forget all queued messages).

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/_lib.sh"

MAILBOX_DIR="$STATE_DIR/mailboxes"
mkdir -p "$MAILBOX_DIR"

mailbox_path() { echo "$MAILBOX_DIR/$1.jsonl"; }
drained_path() { echo "$MAILBOX_DIR/$1.drained.jsonl"; }

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

        # Resolve recipient tmux target for the trigger
        target="$(worker_target "$to")"
        if [ -z "$target" ]; then
            echo "ERROR: unknown recipient '$to' (define WORKER_${to}_TARGET or spawn it)" >&2
            exit 1
        fi

        msg_id="$(date +%s%N)-$(openssl rand -hex 2)"
        created_at="$(date -u +%FT%TZ)"

        # Write FIRST (write-then-notify)
        jq -n -c \
            --arg id "$msg_id" \
            --arg from "$from" \
            --arg to "$to" \
            --arg body "$body" \
            --arg created_at "$created_at" \
            '{message_id:$id, from_worker:$from, to_worker:$to, body:$body, created_at:$created_at}' \
            >> "$(mailbox_path "$to")"

        # Notify AFTER write — short trigger only
        # Recipient can run `mailbox.sh read <self>` to see queued messages.
        TRIGGER="MAILBOX_NOTIFY from=${from} count_with: ${HERE}/mailbox.sh count ${to}"
        if [ "${#TRIGGER}" -le 199 ] && tmux has-session -t "=${target%%:*}" 2>/dev/null; then
            tmux send-keys -t "=$target" "$TRIGGER" 2>/dev/null || true
            sleep 0.4
            tmux send-keys -t "=$target" Enter 2>/dev/null || true
        fi

        echo "$msg_id"
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
            cat "$f" >> "$(drained_path "$who")"
            : > "$f"
        fi
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
        echo "Usage: $0 {send <to> [--from <from>] <body>|read <worker>|peek <worker>|count <worker>|clear <worker>}" >&2
        exit 1
        ;;
esac
