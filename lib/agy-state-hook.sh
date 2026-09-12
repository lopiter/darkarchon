#!/usr/bin/env bash
# Antigravity CLI hook receiver — the agy-side counterpart of state-hook.sh.
#
# Wired in per launch by start-worker-agy.sh through the hooks.json of the
# darkarchon-owned workspace it adds with `--add-dir`, so it only ever runs
# inside a spawned worker's process tree. It still bails out unless
# EE_WORKER_NAME / EE_STATE_DIR are in the environment (agy hands the session's
# env to every hook; verified on 1.2.2), so a copied hooks.json can't make the
# user's own agy sessions write into a team's state dir.
#
# Event → state (agy's payload is camelCase; state-hook.sh expects Claude's
# snake_case, so keys are normalised here before hand-off):
#
#   PreInvocation   → busy   (records conversationId for `agy --conversation`)
#   Stop            → idle   (+ mailbox gate, below)
#
# agy has no session-start, permission or compaction events; the screen
# detector (lib/detectors/agy.py) covers those. Typing into a busy agy pane
# queues the text as the next prompt rather than interrupting the turn, so
# mailbox notifications may be typed at any time — the Stop gate is the
# belt to that suspenders: when a turn ends with messages waiting, it returns
# a Stop `continue` whose reason tells the model to drain its mailbox (agy's
# own keep-working mechanism, capped by agy after a few consecutive
# continuations so it can never loop forever).
#
# Usage (from hooks.json):  agy-state-hook.sh <busy|stop>
# Contract: ALWAYS exit 0 and print exactly one JSON object — agy parses
# stdout and treats anything else as a hook failure.
set -uo pipefail

ACTION="${1:-}"
WORKER="${EE_WORKER_NAME:-}"
SD="${EE_STATE_DIR:-}"
if [ -z "$ACTION" ] || [ -z "$WORKER" ] || [ -z "$SD" ]; then
    printf '{}\n'
    exit 0
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PAYLOAD="$({ command -p cat 2>/dev/null || cat; } 2>/dev/null || true)"

# One python pass decides the state and normalises the payload for
# state-hook.sh. Output: two lines — state and the normalised JSON.
OUT="$(AGY_HOOK_PAYLOAD="$PAYLOAD" python3 - "$ACTION" <<'PY' 2>/dev/null || true
import json, os, sys
action = sys.argv[1]
try:
    p = json.loads(os.environ.get("AGY_HOOK_PAYLOAD", "") or "{}")
    if not isinstance(p, dict):
        p = {}
except Exception:
    p = {}
state = {"busy": "busy", "stop": "idle"}.get(action, "")
detail = ""
if action == "stop":
    if p.get("fullyIdle") is False:
        detail = "background tasks still running"
    err = p.get("error")
    if isinstance(err, str) and err.strip():
        detail = ("error: " + err.strip())[:200]
norm = {
    "hook_event_name": {"busy": "PreInvocation", "stop": "Stop"}.get(action, ""),
    "session_id": p.get("conversationId", "") if isinstance(p.get("conversationId"), str) else "",
    "message": detail,
}
print(state)
print(json.dumps(norm))
PY
)"
if [ -z "$OUT" ]; then
    printf '{}\n'
    exit 0
fi

STATE="$(printf '%s\n' "$OUT" | sed -n 1p)"
NORM="$(printf '%s\n' "$OUT" | sed -n 2p)"

if [ -n "$STATE" ] && [ -x "$HERE/state-hook.sh" ]; then
    # state-hook.sh records CLAUDE_CODE_MESSAGING_SOCKET if present; an agy
    # worker has no such inbox, so make sure a value inherited from whoever
    # spawned the pane can't be mistaken for one.
    printf '%s' "$NORM" | env -u CLAUDE_CODE_MESSAGING_SOCKET EE_STATE_DIR="$SD" "$HERE/state-hook.sh" "$WORKER" "$STATE" >/dev/null 2>&1 || true
fi

# Mailbox gate on turn end.
if [ "$ACTION" = "stop" ] && [ -x "$HERE/mailbox.sh" ]; then
    COUNT="$(STATE_DIR="$SD" "$HERE/mailbox.sh" count "$WORKER" 2>/dev/null | tr -dc '0-9' || true)"
    if [ -n "$COUNT" ] && [ "$COUNT" -gt 0 ] 2>/dev/null; then
        printf '{"decision":"continue","reason":"You have %s unread team message(s). Run: %s/mailbox.sh read %s — then act on what you find before finishing."}\n' \
            "$COUNT" "$HERE" "$WORKER"
        exit 0
    fi
fi
printf '{}\n'
exit 0
