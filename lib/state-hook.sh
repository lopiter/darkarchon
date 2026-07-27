#!/usr/bin/env bash
# Claude Code hook receiver — records a spawned worker's state to
# $STATE_DIR/states/<safe>.json (atomic). Wired in by start-worker-claude.sh via
# `claude --settings <hooks-file>`; each hook event maps to one state:
#
#   SessionStart / Stop  → idle           UserPromptSubmit → busy
#   Notification         → awaiting_user   PreCompact       → compacting
#   SessionEnd           → ended
#
# lib/worker_state.py reads this file as the authoritative state WHILE the
# worker's heartbeat is alive (event-driven, no TUI scraping).
#
# Usage (from the hooks config):  state-hook.sh <worker_name> <state>
# stdin: the hook's JSON payload — its `message` becomes the detail string.
#
# Contract: ALWAYS exit 0. A hook that errors or blocks would stall the worker,
# so every step is best-effort and failures are swallowed. `set -e` is
# deliberately NOT used.
set -uo pipefail

WORKER="${1:-}"
STATE="${2:-}"
[ -z "$WORKER" ] && exit 0
[ -z "$STATE" ] && exit 0

SD="${EE_STATE_DIR:-${STATE_DIR:-}}"
[ -z "$SD" ] && exit 0

# Capture the hook payload from stdin BEFORE the heredoc below claims stdin.
PAYLOAD="$(cat 2>/dev/null || true)"

STATE_HOOK_PAYLOAD="$PAYLOAD" python3 - "$WORKER" "$STATE" "$SD" <<'PY' 2>/dev/null || true
import json, os, sys, time

worker, state, sd = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    payload = json.loads(os.environ.get("STATE_HOOK_PAYLOAD", "") or "{}")
    if not isinstance(payload, dict):
        payload = {}
except Exception:
    payload = {}

detail = payload.get("message") or ""
if not isinstance(detail, str):
    detail = ""
detail = detail.replace("\n", " ").strip()[:200]
event = payload.get("hook_event_name", "") if isinstance(payload, dict) else ""
# The Claude session id, persisted so a dead worker can be respawned with
# `claude --resume <id>` and keep its conversation (spawn-worker --resume-session).
session_id = payload.get("session_id") or ""
if not isinstance(session_id, str):
    session_id = ""

# Claude Code fires Notification BOTH for permission prompts ("Claude needs
# your permission to ...") and for a plain 60s-idle nudge ("Claude is waiting
# for your input"). Only the former blocks dispatch; recording the idle nudge
# as awaiting_user made every worker undispatchable after a minute of rest.
if state == "awaiting_user" and "waiting for your input" in detail.lower():
    state = "idle"

safe = "".join(c if c.isalnum() or c == "_" else "_" for c in worker)
d = os.path.join(sd, "states")
os.makedirs(d, exist_ok=True)
f = os.path.join(d, safe + ".json")
tmp = f + ".tmp." + str(os.getpid())
record = {"state": state, "detail": detail, "event": event, "ts_epoch": int(time.time())}
if session_id:
    record["session_id"] = session_id
else:
    # Keep the last known id — SessionEnd and some events may omit it, and
    # losing it would defeat resume right when it matters (after a shutdown).
    try:
        prev = json.load(open(f))
        if isinstance(prev, dict) and prev.get("session_id"):
            record["session_id"] = prev["session_id"]
    except Exception:
        pass
with open(tmp, "w") as fh:
    json.dump(record, fh)
os.replace(tmp, f)  # atomic
PY

exit 0
