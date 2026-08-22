"""lib/grok-state-hook.sh — grok's hook receiver, exercised end-to-end as a
subprocess with the payload shapes grok 1.0.5 actually sends (camelCase keys,
captured live 2026-08-23)."""

import json
import os
import subprocess
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "lib" / "grok-state-hook.sh"


def run_hook(action: str, payload: dict, tmp_path: Path, worker: str = "w1") -> str:
    env = {**os.environ, "EE_WORKER_NAME": worker, "EE_STATE_DIR": str(tmp_path)}
    env.pop("CLAUDE_CODE_MESSAGING_SOCKET", None)
    r = subprocess.run(
        [str(HOOK), action], input=json.dumps(payload), capture_output=True, text=True, env=env,
    )
    assert r.returncode == 0, r.stderr
    return r.stdout


def state_of(tmp_path: Path, worker: str = "w1") -> dict:
    return json.loads((tmp_path / "states" / f"{worker}.json").read_text())


def test_user_prompt_submit_is_busy_and_records_session_id(tmp_path):
    run_hook("busy", {"hookEventName": "user_prompt_submit", "sessionId": "s-1"}, tmp_path)
    st = state_of(tmp_path)
    assert st["state"] == "busy"
    assert st["session_id"] == "s-1"


def test_stop_is_idle(tmp_path):
    run_hook("busy", {"hookEventName": "user_prompt_submit", "sessionId": "s-1"}, tmp_path)
    out = run_hook("stop", {"hookEventName": "stop", "reason": "end_turn", "stopHookActive": False}, tmp_path)
    assert state_of(tmp_path)["state"] == "idle"
    assert out.strip() == ""  # empty mailbox → no block decision


def test_idle_prompt_notification_is_ignored(tmp_path):
    run_hook("busy", {"hookEventName": "user_prompt_submit"}, tmp_path)
    run_hook("notification", {"hookEventName": "notification", "notificationType": "idle_prompt", "message": "x"}, tmp_path)
    assert state_of(tmp_path)["state"] == "busy"


def test_permission_prompt_notification_is_awaiting_permission(tmp_path):
    run_hook("notification", {"hookEventName": "notification", "notificationType": "permission_prompt", "message": "Allow rm?"}, tmp_path)
    st = state_of(tmp_path)
    assert st["state"] == "awaiting_permission"
    assert st["detail"] == "Allow rm?"


def test_stop_failure_rate_limit_and_auth(tmp_path):
    run_hook("stop-failure", {"hookEventName": "stop_failure", "errorType": "rate_limit"}, tmp_path)
    assert state_of(tmp_path)["state"] == "rate_limited"
    run_hook("stop-failure", {"hookEventName": "stop_failure", "errorType": "authentication_failed"}, tmp_path)
    assert state_of(tmp_path)["state"] == "error"


def test_stop_blocks_when_mailbox_has_messages(tmp_path):
    (tmp_path / "mailboxes").mkdir()
    (tmp_path / "mailboxes" / "w1.jsonl").write_text('{"id":"a","body":"x"}\n{"id":"b","body":"y"}\n')
    out = run_hook("stop", {"hookEventName": "stop", "reason": "end_turn", "stopHookActive": False}, tmp_path)
    d = json.loads(out)
    assert d["decision"] == "block"
    assert "2 unread" in d["reason"] and "mailbox.sh read w1" in d["reason"]


def test_stop_does_not_reblock_while_stop_hook_active(tmp_path):
    (tmp_path / "mailboxes").mkdir()
    (tmp_path / "mailboxes" / "w1.jsonl").write_text('{"id":"a","body":"x"}\n')
    out = run_hook("stop", {"hookEventName": "stop", "reason": "end_turn", "stopHookActive": True}, tmp_path)
    assert out.strip() == ""


def test_stop_does_not_block_session_end_observe_fire(tmp_path):
    (tmp_path / "mailboxes").mkdir()
    (tmp_path / "mailboxes" / "w1.jsonl").write_text('{"id":"a","body":"x"}\n')
    out = run_hook("stop", {"hookEventName": "stop", "reason": "session_end"}, tmp_path)
    assert out.strip() == ""


def test_noop_outside_a_worker_environment(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("EE_")}
    r = subprocess.run([str(HOOK), "busy"], input="{}", capture_output=True, text=True, env=env)
    assert r.returncode == 0 and r.stdout == ""
    assert not (tmp_path / "states").exists()
