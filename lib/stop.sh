#!/usr/bin/env bash
# Stop the team's tmux session.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/_lib.sh"

tmux kill-session -t "$SESSION_NAME" 2>/dev/null && echo "Killed session $SESSION_NAME" || echo "No session $SESSION_NAME running."
