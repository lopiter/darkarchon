#!/usr/bin/env bash
# Grok CLI hook receiver — the grok-side counterpart of state-hook.sh.
#
# Installed ONCE, globally, as ~/.grok/hooks/darkarchon.json by
# start-worker-grok.sh (grok has no per-launch hooks flag; global hook files
# are always trusted and merged). Because it is global it also fires in the
# user's own grok sessions — so the very first thing it does is bail out unless
# EE_WORKER_NAME / EE_STATE_DIR are in the environment, which only a spawned
# darkarchon worker has (grok passes the session's env to every hook; verified
# on 1.0.5).
#
# Event → state (grok's payload is camelCase; state-hook.sh expects Claude's
# snake_case, so keys are normalised here before hand-off):
#
#   SessionStart                       → idle        (records sessionId for --resume)
#   UserPromptSubmit                   → busy
#   Stop / StopCancelled               → idle
#   StopFailure  rate_limit            → rate_limited
#   StopFailure  authentication_failed → error
#   Notification permission_prompt     → awaiting_permission
#   Notification idle_prompt           → (ignored: fires a minute after any turn end)
#   PreCompact                         → compacting
#   SessionEnd                         → ended
#
# Stop additionally acts as the mailbox gate: a grok pane cannot be typed into
# mid-turn (Enter there means "send now" and interrupts the running turn), so
# mailbox.sh leaves messages that arrive while the worker is busy un-notified.
# When the turn ends, this hook sees them outstanding and returns a Stop
# `block` whose reason tells the model to drain its mailbox — grok's own
# keep-working mechanism, no keystrokes involved. `stopHookActive` guards
# against re-blocking the turn that is already draining.
#
# Usage (from the hooks file):  grok-state-hook.sh <state|stop|notification|stop-failure>
# Contract: ALWAYS exit 0 — grok fails open, but a slow or failing hook still
# shows up in the worker's scrollback.
set -uo pipefail

ACTION="${1:-}"
WORKER="${EE_WORKER_NAME:-}"
SD="${EE_STATE_DIR:-}"
if [ -z "$ACTION" ] || [ -z "$WORKER" ] || [ -z "$SD" ]; then
    exit 0
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PAYLOAD="$({ command -p cat 2>/dev/null || cat; } 2>/dev/null || true)"

# One python pass decides the state, normalises the payload for state-hook.sh,
# and (for Stop) whether the mailbox gate may fire. Output: three lines.
OUT="$(GROK_HOOK_PAYLOAD="$PAYLOAD" python3 - "$ACTION" <<'PY' 2>/dev/null || true
import json, os, sys
action = sys.argv[1]
try:
    p = json.loads(os.environ.get("GROK_HOOK_PAYLOAD", "") or "{}")
    if not isinstance(p, dict):
        p = {}
except Exception:
    p = {}
state = action
if action == "stop":
    state = "idle"
elif action == "notification":
    t = str(p.get("notificationType") or "")
    state = {"permission_prompt": "awaiting_permission"}.get(t, "")
elif action == "stop-failure":
    t = str(p.get("errorType") or p.get("error_type") or "")
    state = {"rate_limit": "rate_limited", "authentication_failed": "error"}.get(t, "idle")
msg = p.get("message") or ""
norm = {
    "hook_event_name": p.get("hookEventName", ""),
    "session_id": p.get("sessionId", ""),
    "message": msg if isinstance(msg, str) else "",
}
gate = action == "stop" and not p.get("stopHookActive") and p.get("reason") in (None, "end_turn")
print(state)
print(json.dumps(norm))
print("1" if gate else "0")
PY
)"
[ -z "$OUT" ] && exit 0

STATE="$(printf '%s\n' "$OUT" | sed -n 1p)"
NORM="$(printf '%s\n' "$OUT" | sed -n 2p)"
GATE="$(printf '%s\n' "$OUT" | sed -n 3p)"

if [ -n "$STATE" ] && [ -x "$HERE/state-hook.sh" ]; then
    # state-hook.sh records CLAUDE_CODE_MESSAGING_SOCKET if present; a grok
    # worker has no such inbox, so make sure a value inherited from whoever
    # spawned the pane can't be mistaken for one.
    printf '%s' "$NORM" | env -u CLAUDE_CODE_MESSAGING_SOCKET EE_STATE_DIR="$SD" "$HERE/state-hook.sh" "$WORKER" "$STATE" >/dev/null 2>&1 || true
fi

# Mailbox gate on a genuine turn end.
if [ "$GATE" = "1" ] && [ -x "$HERE/mailbox.sh" ]; then
    COUNT="$(STATE_DIR="$SD" "$HERE/mailbox.sh" count "$WORKER" 2>/dev/null | tr -dc '0-9' || true)"
    if [ -n "$COUNT" ] && [ "$COUNT" -gt 0 ] 2>/dev/null; then
        printf '{"decision":"block","reason":"You have %s unread team message(s). Run: %s/mailbox.sh read %s — then act on what you find before finishing."}\n' \
            "$COUNT" "$HERE" "$WORKER"
    fi
fi
exit 0
