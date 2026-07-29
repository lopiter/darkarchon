#!/usr/bin/env bash
# Worker → User question queue.
#
# Workers call this when they need a human decision or context the
# orchestrator doesn't have. Question is written to
# $STATE_DIR/questions/<id>.json. The orchestrator surfaces pending
# questions on demand via questions.sh list.
#
# Usage:
#   ask.sh "<question body>"                              # uses $EE_WORKER_NAME
#   ask.sh --from <worker> "<question body>"              # explicit sender
#   ask.sh --blocking [--timeout <sec>] "<question>"      # wait for the answer
#
# By default this files the question and returns immediately: the worker keeps
# going and the answer arrives later via mailbox. --blocking instead waits for
# the question to be answered and prints the answer, for decisions the worker
# cannot sensibly proceed without. It polls the question file rather than the
# mailbox so an answer still unblocks it when mailbox delivery fails.
#
# Exit codes:
#   0  question filed (or, with --blocking, answered — answer on stdout)
#   1  bad args / no body / state dir missing
#   2  --blocking: dismissed without an answer
#   3  --blocking: timed out (the question stays pending and answerable)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/_lib.sh"

FROM="${EE_WORKER_NAME:-}"
BLOCKING=0
TIMEOUT="${ASK_TIMEOUT_SEC:-1800}"

# parse args — flags may appear in any order before the body
while [ "$#" -gt 0 ]; do
    case "${1:-}" in
        --from)     FROM="${2:-}"; shift 2 ;;
        --blocking) BLOCKING=1; shift ;;
        --timeout)  TIMEOUT="${2:?--timeout needs seconds}"; shift 2 ;;
        *)          break ;;
    esac
done

if [ "$#" -eq 0 ]; then
    echo "Usage: $0 [--from <worker>] [--blocking [--timeout <sec>]] \"<question>\"" >&2
    exit 1
fi

if [ -z "$FROM" ]; then
    echo "ERROR: sender unknown — set \$EE_WORKER_NAME in env or pass --from <name>" >&2
    exit 1
fi

BODY="$*"

QDIR="$STATE_DIR/questions"
mkdir -p "$QDIR"

QID="$(date +%Y%m%d-%H%M%S)-$(openssl rand -hex 2)"
QFILE="$QDIR/$QID.json"

CREATED_AT="$(date -u +%FT%TZ)"

if command -v jq >/dev/null 2>&1; then
    jq -n -c \
        --arg id "$QID" \
        --arg from "$FROM" \
        --arg body "$BODY" \
        --arg created_at "$CREATED_AT" \
        --argjson blocking "$BLOCKING" \
        '{question_id:$id, from_worker:$from, body:$body, created_at:$created_at,
          status:"pending", blocking:($blocking == 1)}' \
        > "$QFILE"
else
    # jq fallback — escape minimal
    esc() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g; s/$/\\n/' | tr -d '\n' | sed 's/\\n$//'; }
    [ "$BLOCKING" -eq 1 ] && BJSON=true || BJSON=false
    {
        printf '{"question_id":"%s","from_worker":"%s","body":"%s","created_at":"%s","status":"pending","blocking":%s}\n' \
            "$QID" "$FROM" "$(esc "$BODY")" "$CREATED_AT" "$BJSON"
    } > "$QFILE"
fi

if [ "$BLOCKING" -eq 0 ]; then
    echo "$QID"
    exit 0
fi

# ── Blocking mode: wait for questions.sh to resolve this question ───────────
# Watch the question file, which `answer` writes before it notifies. Polling the
# mailbox instead would strand us whenever delivery fails.
echo "ASK_BLOCKING $QID — waiting up to ${TIMEOUT}s for an answer" >&2
DEADLINE=$(( $(date +%s) + TIMEOUT ))
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
    STATUS="$(jq -r '.status // "pending"' "$QFILE" 2>/dev/null || echo pending)"
    case "$STATUS" in
        answered)
            jq -r '.answer // ""' "$QFILE"
            exit 0 ;;
        dismissed)
            echo "ASK_DISMISSED $QID: $(jq -r '.dismiss_reason // "no reason given"' "$QFILE" 2>/dev/null)" >&2
            exit 2 ;;
    esac
    sleep "${ASK_POLL_INTERVAL:-3}"
done

# Deliberately left pending: the human can still answer it, and the worker is
# free to decide for itself rather than hang.
echo "ASK_TIMEOUT $QID (no answer within ${TIMEOUT}s; question remains pending)" >&2
exit 3
