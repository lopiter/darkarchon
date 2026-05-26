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
#
# Exit codes:
#   0  question filed
#   1  bad args / no body / state dir missing
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/_lib.sh"

FROM="${EE_WORKER_NAME:-}"

# parse args
if [ "${1:-}" = "--from" ]; then
    FROM="${2:-}"
    shift 2
fi

if [ "$#" -eq 0 ]; then
    echo "Usage: $0 [--from <worker>] \"<question>\"" >&2
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
        '{question_id:$id, from_worker:$from, body:$body, created_at:$created_at, status:"pending"}' \
        > "$QFILE"
else
    # jq fallback — escape minimal
    esc() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g; s/$/\\n/' | tr -d '\n' | sed 's/\\n$//'; }
    {
        printf '{"question_id":"%s","from_worker":"%s","body":"%s","created_at":"%s","status":"pending"}\n' \
            "$QID" "$FROM" "$(esc "$BODY")" "$CREATED_AT"
    } > "$QFILE"
fi

echo "$QID"
