"""lib/gemini-state-hook.sh — gemini's hook receiver, exercised end-to-end as a
subprocess with the payload shapes gemini-cli 0.59.0 actually sends (snake_case
keys, captured live 2026-09-12)."""

import json
import os
import subprocess
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "lib" / "gemini-state-hook.sh"

BASE = {
    "session_id": "2d7dfd7c-eb9c-4609-893b-dae042117438",
    "transcript_path": "/Users/u/.gemini/tmp/work/chats/session-2026-09-12T12-52-2d7dfd7c.jsonl",
    "cwd": "/Users/u/repo",
    "timestamp": "2026-09-12T12:52:09.235Z",
}


def run_hook(action: str, payload: dict, tmp_path: Path, worker: str = "w1", extra_env: dict | None = None) -> str:
    env = {**os.environ, "EE_WORKER_NAME": worker, "EE_STATE_DIR": str(tmp_path)}
    env.pop("CLAUDE_CODE_MESSAGING_SOCKET", None)
    env.pop("EE_GEMINI_CONTRACT", None)
    if extra_env:
        env.update(extra_env)
    r = subprocess.run(
        [str(HOOK), action], input=json.dumps(payload), capture_output=True, text=True, env=env,
    )
    assert r.returncode == 0, r.stderr
    return r.stdout


def state_of(tmp_path: Path, worker: str = "w1") -> dict:
    return json.loads((tmp_path / "states" / f"{worker}.json").read_text())


def test_session_start_is_idle_and_records_session_id(tmp_path):
    out = run_hook("session-start", {**BASE, "hook_event_name": "SessionStart", "source": "startup"}, tmp_path)
    st = state_of(tmp_path)
    assert st["state"] == "idle"
    assert st["session_id"] == BASE["session_id"]
    assert out.strip() == ""  # no contract file → nothing to inject


def test_session_start_returns_contract_as_additional_context(tmp_path):
    contract = tmp_path / "contract.md"
    contract.write_text("# Team contract\nBe nice.\n")
    out = run_hook(
        "session-start", {**BASE, "hook_event_name": "SessionStart", "source": "startup"}, tmp_path,
        extra_env={"EE_GEMINI_CONTRACT": str(contract)},
    )
    d = json.loads(out)
    assert d["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert d["hookSpecificOutput"]["additionalContext"] == "# Team contract\nBe nice.\n"
    assert state_of(tmp_path)["state"] == "idle"


def test_before_agent_is_busy(tmp_path):
    run_hook("busy", {**BASE, "hook_event_name": "BeforeAgent", "prompt": "do x"}, tmp_path)
    assert state_of(tmp_path)["state"] == "busy"


def test_after_agent_is_idle(tmp_path):
    run_hook("busy", {**BASE, "hook_event_name": "BeforeAgent", "prompt": "do x"}, tmp_path)
    out = run_hook(
        "after-agent",
        {**BASE, "hook_event_name": "AfterAgent", "prompt": "do x", "prompt_response": "done", "stop_hook_active": False},
        tmp_path,
    )
    assert state_of(tmp_path)["state"] == "idle"
    assert out.strip() == ""  # empty mailbox → no block decision


def test_tool_permission_notification_is_awaiting_permission(tmp_path):
    run_hook(
        "notification",
        {**BASE, "hook_event_name": "Notification", "notification_type": "ToolPermission", "message": "Allow rm?", "details": {}},
        tmp_path,
    )
    st = state_of(tmp_path)
    assert st["state"] == "awaiting_permission"
    assert st["detail"] == "Allow rm?"


def test_other_notification_is_ignored(tmp_path):
    run_hook("busy", {**BASE, "hook_event_name": "BeforeAgent", "prompt": "do x"}, tmp_path)
    run_hook(
        "notification",
        {**BASE, "hook_event_name": "Notification", "notification_type": "SomethingElse", "message": "x"},
        tmp_path,
    )
    assert state_of(tmp_path)["state"] == "busy"


def test_compacting_and_ended(tmp_path):
    run_hook("compacting", {**BASE, "hook_event_name": "PreCompress", "trigger": "auto"}, tmp_path)
    assert state_of(tmp_path)["state"] == "compacting"
    run_hook("ended", {**BASE, "hook_event_name": "SessionEnd", "reason": "exit"}, tmp_path)
    assert state_of(tmp_path)["state"] == "ended"


def test_after_agent_blocks_when_mailbox_has_messages(tmp_path):
    (tmp_path / "mailboxes").mkdir()
    (tmp_path / "mailboxes" / "w1.jsonl").write_text('{"id":"a","body":"x"}\n{"id":"b","body":"y"}\n')
    out = run_hook(
        "after-agent",
        {**BASE, "hook_event_name": "AfterAgent", "prompt": "p", "prompt_response": "r", "stop_hook_active": False},
        tmp_path,
    )
    d = json.loads(out)
    assert d["decision"] == "block"
    assert "2 unread" in d["reason"] and "mailbox.sh read w1" in d["reason"]


def test_after_agent_does_not_reblock_while_stop_hook_active(tmp_path):
    (tmp_path / "mailboxes").mkdir()
    (tmp_path / "mailboxes" / "w1.jsonl").write_text('{"id":"a","body":"x"}\n')
    out = run_hook(
        "after-agent",
        {**BASE, "hook_event_name": "AfterAgent", "prompt": "p", "prompt_response": "r", "stop_hook_active": True},
        tmp_path,
    )
    assert out.strip() == ""


def test_noop_outside_a_worker_environment(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("EE_")}
    r = subprocess.run([str(HOOK), "busy"], input="{}", capture_output=True, text=True, env=env)
    assert r.returncode == 0 and r.stdout == ""
    assert not (tmp_path / "states").exists()
