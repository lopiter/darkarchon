#!/usr/bin/env bash
# Gemini CLI hook receiver — the gemini-side counterpart of state-hook.sh.
#
# Wired in per launch by start-worker-gemini.sh through a settings file handed
# to gemini via GEMINI_CLI_SYSTEM_SETTINGS_PATH, so it only ever runs inside a
# spawned worker's process tree. It still bails out unless EE_WORKER_NAME /
# EE_STATE_DIR are in the environment (gemini passes the session's env to
# every hook; verified on 0.59.0), so a copied settings file can't make the
# user's own gemini sessions write into a team's state dir.
#
# Event → state (gemini's payload is snake_case like Claude Code's, so it is
# handed to state-hook.sh mostly as-is):
#
#   SessionStart                  → idle        (records session_id for --resume)
#                                   + returns the team contract as additionalContext
#   BeforeAgent                   → busy
#   AfterAgent                    → idle        (+ mailbox gate, below)
#   Notification ToolPermission   → awaiting_permission
#   Notification <anything else>  → (ignored: informational)
#   PreCompress                   → compacting
#   SessionEnd                    → ended
#
# AfterAgent doubles as the mailbox gate: a gemini pane cannot be typed into
# mid-turn without the keystrokes landing in the composer, so mailbox.sh leaves
# messages that arrive while the worker is busy un-notified. When the turn ends,
# this hook sees them outstanding and returns an AfterAgent `block` whose reason
# tells the model to drain its mailbox — gemini feeds the reason back and
# re-runs the turn, no keystrokes involved. `stop_hook_active` guards against
# re-blocking the turn that is already draining.
#
# Usage (from the settings file):
#   gemini-state-hook.sh <session-start|busy|after-agent|notification|compacting|ended>
# Contract: ALWAYS exit 0 and print nothing but JSON — gemini fails open, but a
# hook that prints plain text to stdout breaks its output parsing.
set -uo pipefail

ACTION="${1:-}"
WORKER="${EE_WORKER_NAME:-}"
SD="${EE_STATE_DIR:-}"
if [ -z "$ACTION" ] || [ -z "$WORKER" ] || [ -z "$SD" ]; then
    exit 0
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PAYLOAD="$({ command -p cat 2>/dev/null || cat; } 2>/dev/null || true)"

# One python pass decides the state and (for after-agent) whether the mailbox
# gate may fire. Output: two lines — state (may be empty) and gate flag.
OUT="$(GEMINI_HOOK_PAYLOAD="$PAYLOAD" python3 - "$ACTION" <<'PY' 2>/dev/null || true
import json, os, sys
action = sys.argv[1]
try:
    p = json.loads(os.environ.get("GEMINI_HOOK_PAYLOAD", "") or "{}")
    if not isinstance(p, dict):
        p = {}
except Exception:
    p = {}
state = action
if action in ("session-start", "after-agent"):
    state = "idle"
elif action == "notification":
    t = str(p.get("notification_type") or "")
    state = {"ToolPermission": "awaiting_permission"}.get(t, "")
gate = action == "after-agent" and not p.get("stop_hook_active")
print(state)
print("1" if gate else "0")
PY
)"
[ -z "$OUT" ] && exit 0

STATE="$(printf '%s\n' "$OUT" | sed -n 1p)"
GATE="$(printf '%s\n' "$OUT" | sed -n 2p)"

if [ -n "$STATE" ] && [ -x "$HERE/state-hook.sh" ]; then
    # state-hook.sh records CLAUDE_CODE_MESSAGING_SOCKET if present; a gemini
    # worker has no such inbox, so make sure a value inherited from whoever
    # spawned the pane can't be mistaken for one.
    printf '%s' "$PAYLOAD" | env -u CLAUDE_CODE_MESSAGING_SOCKET EE_STATE_DIR="$SD" "$HERE/state-hook.sh" "$WORKER" "$STATE" >/dev/null 2>&1 || true
fi

# SessionStart: hand the team contract back as additionalContext. The file is
# written by start-worker-gemini.sh and named in EE_GEMINI_CONTRACT.
if [ "$ACTION" = "session-start" ]; then
    CONTRACT="${EE_GEMINI_CONTRACT:-}"
    if [ -n "$CONTRACT" ] && [ -s "$CONTRACT" ]; then
        python3 - "$CONTRACT" <<'PY' 2>/dev/null || true
import json, sys
text = open(sys.argv[1], encoding="utf-8", errors="replace").read()
print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}))
PY
    fi
    exit 0
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
