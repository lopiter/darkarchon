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
#
# `command -p cat` resolves from the shell's default PATH rather than the
# inherited one. A worker launched with a stripped PATH would otherwise get
# exit 127 and a broken pipe mid-write, and a repo that happens to ship its own
# `cat` executable would capture the hook payload. Falls back to a bare `cat`
# for the rare host where `command -p` finds nothing.
PAYLOAD="$({ command -p cat 2>/dev/null || cat; } 2>/dev/null || true)"

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
# The session's cross-session messaging inbox socket. Claude Code exports it
# to every hook before running it; recorded so senders (peer_post in _lib.sh)
# can post messages straight into the session instead of typing into the pane.
sock = os.environ.get("CLAUDE_CODE_MESSAGING_SOCKET") or ""

# Claude Code fires Notification BOTH for permission prompts ("Claude needs
# your permission to ...") and for a plain 60s-idle nudge ("Claude is waiting
# for your input"). Only the former blocks dispatch; recording the idle nudge
# as awaiting_user made every worker undispatchable after a minute of rest.
#
# The PermissionRequest hook now reports real permission prompts directly as
# awaiting_permission, so this string match is only a fallback for the
# Notification path — kept because it still fires on Claude Code builds or
# settings merges where PermissionRequest does not reach us.
if state == "awaiting_user" and "waiting for your input" in detail.lower():
    state = "idle"

safe = "".join(c if c.isalnum() or c == "_" else "_" for c in worker)
d = os.path.join(sd, "states")
os.makedirs(d, exist_ok=True)
f = os.path.join(d, safe + ".json")
tmp = f + ".tmp." + str(os.getpid())
record = {"state": state, "detail": detail, "event": event, "ts_epoch": int(time.time())}
# Keep the last known value when an event omits one — SessionEnd and some
# events drop session_id, and losing it would defeat resume right when it
# matters (after a shutdown). The socket path gets the same treatment so a
# late hook without the env var can't erase a working delivery address.
prev = {}
try:
    prev = json.load(open(f))
    if not isinstance(prev, dict):
        prev = {}
except Exception:
    pass
for key, val in (("session_id", session_id), ("messaging_socket", sock)):
    if val:
        record[key] = val
    elif prev.get(key):
        record[key] = prev[key]
with open(tmp, "w") as fh:
    json.dump(record, fh)
os.replace(tmp, f)  # atomic
PY

exit 0
