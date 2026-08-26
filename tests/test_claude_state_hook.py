"""lib/state-hook.sh — Claude Code's hook receiver, exercised end-to-end as a
subprocess.

The interesting cases all live on the Notification event. Claude Code fires it
for several unrelated things and only one of them blocks on a human, so the
receiver has to tell them apart instead of recording every notification as
awaiting_user (which pinned workers as undispatchable and made the dashboard
show a question nobody asked).
"""

import json
import os
import subprocess
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "lib" / "state-hook.sh"


def run_hook(state: str, payload: dict, tmp_path: Path, worker: str = "w1", sock: str = "") -> None:
    env = {**os.environ, "EE_STATE_DIR": str(tmp_path)}
    env.pop("STATE_DIR", None)
    env.pop("CLAUDE_CODE_MESSAGING_SOCKET", None)
    if sock:
        env["CLAUDE_CODE_MESSAGING_SOCKET"] = sock
    r = subprocess.run(
        [str(HOOK), worker, state],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
    )
    assert r.returncode == 0, r.stderr


def notify(message: str, tmp_path: Path, **kw) -> None:
    """The Notification hook as start-worker-claude.sh wires it: state awaiting_user."""
    run_hook("awaiting_user", {"hook_event_name": "Notification", "message": message}, tmp_path, **kw)


def state_of(tmp_path: Path, worker: str = "w1") -> dict:
    return json.loads((tmp_path / "states" / f"{worker}.json").read_text())


def exists(tmp_path: Path, worker: str = "w1") -> bool:
    return (tmp_path / "states" / f"{worker}.json").exists()


# ── the plain event → state mapping ────────────────────────────────────────
def test_session_start_records_idle_and_session_id(tmp_path):
    run_hook("idle", {"hook_event_name": "SessionStart", "session_id": "s-1"}, tmp_path)
    st = state_of(tmp_path)
    assert st["state"] == "idle"
    assert st["session_id"] == "s-1"


def test_user_prompt_submit_records_busy(tmp_path):
    run_hook("busy", {"hook_event_name": "UserPromptSubmit"}, tmp_path)
    assert state_of(tmp_path)["state"] == "busy"


# ── Notification: the one shape that really blocks ─────────────────────────
def test_permission_notification_is_awaiting_user(tmp_path):
    run_hook("busy", {"hook_event_name": "UserPromptSubmit"}, tmp_path)
    notify("Claude needs your permission to use Bash", tmp_path)
    st = state_of(tmp_path)
    assert st["state"] == "awaiting_user"
    assert st["detail"] == "Claude needs your permission to use Bash"


# ── Notification: the 60s idle nudge ───────────────────────────────────────
def test_idle_nudge_notification_records_idle(tmp_path):
    # Explicitly repairs a stale non-idle record: the message means Claude is
    # sitting at the prompt, whatever the last event claimed.
    run_hook("busy", {"hook_event_name": "UserPromptSubmit"}, tmp_path)
    notify("Claude is waiting for your input", tmp_path)
    assert state_of(tmp_path)["state"] == "idle"


# ── Notification: everything else is informational ─────────────────────────
def test_login_notification_does_not_block_an_idle_worker(tmp_path):
    # Regression: `/login` in a worker's pane fires Notification("Claude Code
    # login successful"), which used to be recorded as awaiting_user. Nothing
    # else fires a hook afterwards (/login and /model run no turn), so the
    # worker sat "awaiting" on the dashboard and refused dispatches for hours.
    run_hook("idle", {"hook_event_name": "Stop"}, tmp_path)
    notify("Claude Code login successful", tmp_path)
    assert state_of(tmp_path)["state"] == "idle"


def test_informational_notification_leaves_a_busy_worker_busy(tmp_path):
    run_hook("busy", {"hook_event_name": "UserPromptSubmit"}, tmp_path)
    notify("Claude Code login successful", tmp_path)
    st = state_of(tmp_path)
    assert st["state"] == "busy"
    assert st["detail"] == ""  # prev detail kept, notification message dropped


def test_informational_notification_keeps_the_state_timestamp(tmp_path):
    # ts_epoch is a transition marker (orchestrators.py measures turn duration
    # and dedupes alerts on it). An informational notification is not a
    # transition, so it must not restart the clock.
    run_hook("busy", {"hook_event_name": "UserPromptSubmit"}, tmp_path)
    f = tmp_path / "states" / "w1.json"
    f.write_text(json.dumps({**json.loads(f.read_text()), "ts_epoch": 1_700_000_000}))
    notify("Claude Code login successful", tmp_path)
    assert state_of(tmp_path)["ts_epoch"] == 1_700_000_000


def test_informational_notification_still_refreshes_the_delivery_address(tmp_path):
    run_hook("busy", {"hook_event_name": "UserPromptSubmit", "session_id": "s-1"}, tmp_path)
    notify("Claude Code login successful", tmp_path, sock="/tmp/cc-socks/9.sock")
    st = state_of(tmp_path)
    assert st["state"] == "busy"
    assert st["session_id"] == "s-1"
    assert st["messaging_socket"] == "/tmp/cc-socks/9.sock"


def test_informational_notification_with_no_prior_record_writes_nothing(tmp_path):
    # No prior state means nothing to preserve, and the notification itself says
    # nothing about the worker. Leaving the file absent lets worker_state.py
    # fall back to the screen instead of recording a guess.
    notify("Claude Code login successful", tmp_path)
    assert not exists(tmp_path)
