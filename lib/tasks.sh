#!/usr/bin/env bash
# Manage / inspect dispatched tasks recorded in $STATE_DIR/tasks.db.
#
# Thin wrapper around lib/task_store.py — gives the legacy CLI shape while
# all storage now lives in SQLite. Json files under $STATE_DIR/tasks/ are
# still written by dispatch.sh for backward compat but not read here.
#
# Usage:
#   tasks.sh list              List all tasks (newest first) — id, worker, status, dispatched_at
#   tasks.sh today             List today's tasks
#   tasks.sh failed            List failed/timeout tasks
#   tasks.sh show <id>         Print the full JSON for a task
#   tasks.sh result <id>       Print just the result text
#   tasks.sh prune-old [DAYS]  Delete tasks older than DAYS (default 30)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/_lib.sh"

TS="python3 $HERE/task_store.py"

cmd="${1:-list}"
case "$cmd" in
    list)
        $TS list
        ;;
    today)
        # Midnight UTC today, ISO 8601.
        TODAY="$(date -u +%FT00:00:00Z)"
        $TS list --since "$TODAY"
        ;;
    failed)
        # Two queries — failed + timeout. SQLite store has no OR helper in CLI,
        # so we list each separately and stitch the output (header once).
        printf '%-22s %-15s %-12s %s\n' "ID" "WORKER" "STATUS" "DISPATCHED"
        echo "----------------------------------------------------------------------"
        $TS list --status failed --format json | python3 -c "
import json, sys
for r in json.load(sys.stdin):
    err = (r.get('error') or '').replace('\n', ' ')[:60]
    print(f\"{r['id']:<22} {(r['worker'] or '')[:15]:<15} {r['status']:<12} {err}\")"
        $TS list --status timeout --format json | python3 -c "
import json, sys
for r in json.load(sys.stdin):
    err = (r.get('error') or '').replace('\n', ' ')[:60]
    print(f\"{r['id']:<22} {(r['worker'] or '')[:15]:<15} {r['status']:<12} {err}\")"
        ;;
    show)
        id="${2:?usage: tasks.sh show <id>}"
        $TS get "$id"
        ;;
    result)
        id="${2:?usage: tasks.sh result <id>}"
        $TS get "$id" | python3 -c "import json, sys; print((json.load(sys.stdin).get('result') or ''))"
        ;;
    prune-old)
        days="${2:-30}"
        echo "Deleting tasks older than $days days..."
        $TS prune "$days"
        ;;
    *)
        echo "Unknown command: $cmd" >&2
        echo "Usage: $0 {list|today|failed|show <id>|result <id>|prune-old [days]}" >&2
        exit 1
        ;;
esac
