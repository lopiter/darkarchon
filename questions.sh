#!/usr/bin/env bash
# Orchestrator-side: inspect and answer worker questions filed via ask.sh.
#
# Usage:
#   questions.sh list                       List all pending questions
#   questions.sh list --all                 Include answered/dismissed
#   questions.sh show <id>                  Print one question's JSON
#   questions.sh answer <id> "<answer>"     Send answer to the asking worker via mailbox
#                                           and mark question answered
#   questions.sh dismiss <id> ["<reason>"]  Mark a question as dismissed without answer
#   questions.sh clear-answered             Remove answered/dismissed records
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/lib/_lib.sh"

QDIR="$STATE_DIR/questions"
MAILBOX_SH="$HERE/lib/mailbox.sh"

mkdir -p "$QDIR"

cmd="${1:-list}"

case "$cmd" in
    list)
        include_all=0
        if [ "${2:-}" = "--all" ]; then include_all=1; fi
        shopt -s nullglob
        files=("$QDIR"/*.json)
        if [ "${#files[@]}" -eq 0 ]; then
            echo "(no questions)"
            exit 0
        fi
        printf '%-25s  %-12s  %-9s  %s\n' "ID" "FROM" "STATUS" "BODY (head)"
        printf '%-25s  %-12s  %-9s  %s\n' "-------------------------" "------------" "---------" "------------"
        for f in "${files[@]}"; do
            id=$(jq -r '.question_id' "$f" 2>/dev/null || echo "?")
            from=$(jq -r '.from_worker' "$f" 2>/dev/null || echo "?")
            status=$(jq -r '.status' "$f" 2>/dev/null || echo "?")
            body=$(jq -r '.body' "$f" 2>/dev/null | tr '\n' ' ' | cut -c1-80)
            if [ "$include_all" -eq 0 ] && [ "$status" != "pending" ]; then
                continue
            fi
            printf '%-25s  %-12s  %-9s  %s\n' "$id" "$from" "$status" "$body"
        done
        ;;

    show)
        id="${2:?Usage: $0 show <id>}"
        f="$QDIR/$id.json"
        if [ ! -f "$f" ]; then
            echo "ERROR: not found: $id" >&2
            exit 1
        fi
        jq . "$f" 2>/dev/null || cat "$f"
        ;;

    answer)
        id="${2:?Usage: $0 answer <id> \"<answer>\"}"
        shift 2
        if [ "$#" -eq 0 ]; then
            echo "Usage: $0 answer <id> \"<answer>\"" >&2
            exit 1
        fi
        ans="$*"
        f="$QDIR/$id.json"
        if [ ! -f "$f" ]; then
            echo "ERROR: not found: $id" >&2
            exit 1
        fi
        from=$(jq -r '.from_worker' "$f")
        # send via mailbox so the worker drains it on next NOTIFY/check
        "$MAILBOX_SH" send "$from" --from "orchestrator" "ANSWER to $id: $ans" >/dev/null
        # mark answered
        tmp="$f.tmp"
        jq --arg ans "$ans" --arg ts "$(date -u +%FT%TZ)" \
            '. + {status:"answered", answer:$ans, answered_at:$ts}' "$f" > "$tmp" && mv "$tmp" "$f"
        echo "answered $id (delivered to $from via mailbox)"
        ;;

    dismiss)
        id="${2:?Usage: $0 dismiss <id> [\"<reason>\"]}"
        reason="${3:-dismissed}"
        f="$QDIR/$id.json"
        if [ ! -f "$f" ]; then
            echo "ERROR: not found: $id" >&2
            exit 1
        fi
        tmp="$f.tmp"
        jq --arg r "$reason" --arg ts "$(date -u +%FT%TZ)" \
            '. + {status:"dismissed", dismiss_reason:$r, dismissed_at:$ts}' "$f" > "$tmp" && mv "$tmp" "$f"
        echo "dismissed $id"
        ;;

    clear-answered)
        n=0
        shopt -s nullglob
        for f in "$QDIR"/*.json; do
            status=$(jq -r '.status' "$f" 2>/dev/null || echo "")
            if [ "$status" = "answered" ] || [ "$status" = "dismissed" ]; then
                rm -f "$f"
                n=$((n+1))
            fi
        done
        echo "removed $n records"
        ;;

    *)
        echo "Usage: $0 {list [--all]|show <id>|answer <id> \"<text>\"|dismiss <id> [\"<reason>\"]|clear-answered}" >&2
        exit 1
        ;;
esac
