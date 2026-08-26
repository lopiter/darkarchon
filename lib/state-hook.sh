#!/usr/bin/env bash
# Claude Code hook receiver — records a spawned worker's state to
# $STATE_DIR/states/<safe>.json (atomic). Wired in by start-worker-claude.sh via
# `claude --settings <hooks-file>`; each hook event maps to one state:
#
#   SessionStart / Stop  → idle           UserPromptSubmit → busy
#   Notification         → see below      PreCompact       → compacting
#   SessionEnd           → ended
#
# Notification is the one event that does not map to a single state — Claude
# Code fires it for several unrelated things. The receiver splits them by
# message; see the comment on NOTIFY_BLOCKING below.
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
import json, os, re, sys, time

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

safe = "".join(c if c.isalnum() or c == "_" else "_" for c in worker)
d = os.path.join(sd, "states")
f = os.path.join(d, safe + ".json")

# The last record. Read before deciding the new state: an informational
# Notification (below) keeps it, and every event inherits its session_id /
# messaging_socket when the payload omits them.
prev = {}
try:
    prev = json.load(open(f))
    if not isinstance(prev, dict):
        prev = {}
except Exception:
    pass

# Claude Code fires Notification for several unrelated things, and only one of
# them blocks on a human:
#
#   "Claude needs your permission to use Bash"  — blocked, must not take work
#   "Claude is waiting for your input"          — the 60s idle nudge: at the prompt
#   "Claude Code login successful"              — informational, says nothing
#
# Recording all of them as awaiting_user is what broke twice: the idle nudge
# made every worker undispatchable after a minute of rest (fixed 2026-07 by
# blacklisting that one message), then `/login` in a worker's pane pinned it
# "awaiting" for hours — no turn runs after a slash command, so no later hook
# corrects the file. Blacklisting known-benign messages loses that race by
# design, so the test is inverted: only a message that NAMES a block blocks,
# the nudge maps to idle, and anything else leaves the record alone.
#
# The PermissionRequest hook reports real permission prompts directly as
# awaiting_permission; this path is the fallback for builds and settings merges
# where that hook does not reach us.
NOTIFY_BLOCKING = re.compile(r"permission|approval", re.I)
NOTIFY_IDLE = re.compile(r"waiting for your input", re.I)

ts = int(time.time())
if state == "awaiting_user" and not NOTIFY_BLOCKING.search(detail):
    if NOTIFY_IDLE.search(detail):
        # An explicit "I am at the prompt" — repairs a stale busy/awaiting record.
        state = "idle"
    elif prev.get("state"):
        # Informational: carry the last known state forward untouched. ts_epoch
        # marks a transition (orchestrators.py measures turn length and dedupes
        # alerts on it), and this is not one.
        state, detail, event = prev["state"], prev.get("detail", ""), prev.get("event", "")
        prev_ts = prev.get("ts_epoch")
        ts = prev_ts if isinstance(prev_ts, int) else ts
    else:
        # Nothing known and nothing learned. Writing a guess would outrank the
        # screen in worker_state.py; leaving the file absent lets it scrape.
        sys.exit(0)

os.makedirs(d, exist_ok=True)
tmp = f + ".tmp." + str(os.getpid())
record = {"state": state, "detail": detail, "event": event, "ts_epoch": ts}
# Keep the last known value when an event omits one — SessionEnd and some
# events drop session_id, and losing it would defeat resume right when it
# matters (after a shutdown). The socket path gets the same treatment so a
# late hook without the env var can't erase a working delivery address.
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
