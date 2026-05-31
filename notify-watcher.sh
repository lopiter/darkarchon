#!/usr/bin/env bash
# notify-watcher.sh — consume hub SSE events and trigger macOS desktop notifications.
#
# Run on the main PC. Requires either terminal-notifier (preferred) or osascript.
#
# Usage:
#   notify-watcher.sh [hub_url]
#
# Defaults to http://127.0.0.1:<derived-port>/api/events.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/lib/_lib.sh"

PORT_OFFSET=$(printf '%s' "$SESSION_NAME" | cksum | awk '{print $1 % 100}')
DEFAULT_PORT=$((8765 + PORT_OFFSET))
HUB_URL="${1:-http://127.0.0.1:$DEFAULT_PORT}"

notify() {
    local title="$1"
    local body="$2"
    if command -v terminal-notifier >/dev/null 2>&1; then
        terminal-notifier -title "$title" -message "$body" -group "${SESSION_NAME}" >/dev/null 2>&1 || true
    else
        osascript -e "display notification \"${body//\"/\\\"}\" with title \"${title//\"/\\\"}\"" >/dev/null 2>&1 || true
    fi
}

echo "[notify-watcher] subscribing to $HUB_URL/api/events"

curl -N -sS "$HUB_URL/api/events" | while IFS= read -r line; do
    case "$line" in
        "data: "*)
            json="${line#data: }"
            type=$(printf '%s' "$json" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(d.get('type',''))" 2>/dev/null || echo "")
            case "$type" in
                state_change)
                    fr=$(printf '%s' "$json" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(d.get('from',''))" 2>/dev/null)
                    to=$(printf '%s' "$json" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(d.get('to',''))" 2>/dev/null)
                    if [ "$fr" = "busy" ] && [ "$to" = "idle" ]; then
                        # Suppress when the user is currently viewing this pane:
                        # you're watching it finish and about to type, so a
                        # desktop alert is just noise. Notify only for panes you
                        # are NOT looking at (background workers, other windows).
                        focused=$(printf '%s' "$json" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print((d.get('worker') or {}).get('focused', False))" 2>/dev/null)
                        if [ "$focused" != "True" ]; then
                            host=$(printf '%s' "$json" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(d.get('host',''))" 2>/dev/null)
                            name=$(printf '%s' "$json" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print((d.get('worker') or {}).get('name',''))" 2>/dev/null)
                            notify "✓ $name finished" "$host"
                        fi
                    fi
                    ;;
                new_question)
                    from=$(printf '%s' "$json" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(d.get('from_worker',''))" 2>/dev/null)
                    body=$(printf '%s' "$json" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(d.get('body',''))" 2>/dev/null)
                    notify "❓ Question from $from" "$body"
                    ;;
            esac
            ;;
    esac
done
