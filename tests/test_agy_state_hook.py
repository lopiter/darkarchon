"""lib/agy-state-hook.sh — agy's hook receiver, exercised end-to-end as a
subprocess with the payload shapes agy 1.2.2 actually sends (camelCase keys,
captured live 2026-09-12)."""

import json
import os
import subprocess
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "lib" / "agy-state-hook.sh"

CONV = "763b9576-ea38-42c8-ab21-1ff343b6ef35"
BASE = {
    "artifactDirectoryPath": f"/Users/u/.gemini/antigravity-cli/brain/{CONV}",
    "conversationId": CONV,
    "modelName": "gemini-3.8-flash-high",
    "transcriptPath": f"/Users/u/.gemini/antigravity-cli/brain/{CONV}/.system_generated/logs/transcript_full.jsonl",
    "workspacePaths": ["/Users/u/repo", "/Users/u/.darkarchon/t/agy/w1"],
}
PRE = {**BASE, "initialNumSteps": 1, "invocationNum": 0}
STOP = {**BASE, "error": "", "executionNum": 0, "fullyIdle": True, "terminationReason": "NO_TOOL_CALL"}


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


def test_pre_invocation_is_busy_and_records_conversation_id(tmp_path):
    out = run_hook("busy", PRE, tmp_path)
    st = state_of(tmp_path)
    assert st["state"] == "busy"
    assert st["session_id"] == CONV
    assert json.loads(out) == {}


def test_stop_is_idle(tmp_path):
    run_hook("busy", PRE, tmp_path)
    out = run_hook("stop", STOP, tmp_path)
    assert state_of(tmp_path)["state"] == "idle"
    assert json.loads(out) == {}  # empty mailbox → no continue decision


def test_stop_with_background_tasks_notes_it(tmp_path):
    run_hook("stop", {**STOP, "fullyIdle": False}, tmp_path)
    st = state_of(tmp_path)
    assert st["state"] == "idle"
    assert "background" in st["detail"]


def test_stop_with_error_keeps_the_error_text(tmp_path):
    run_hook("stop", {**STOP, "error": "quota exceeded", "terminationReason": "error"}, tmp_path)
    assert "quota exceeded" in state_of(tmp_path)["detail"]


def test_stop_continues_when_mailbox_has_messages(tmp_path):
    (tmp_path / "mailboxes").mkdir()
    (tmp_path / "mailboxes" / "w1.jsonl").write_text('{"id":"a","body":"x"}\n{"id":"b","body":"y"}\n')
    out = run_hook("stop", STOP, tmp_path)
    d = json.loads(out)
    assert d["decision"] == "continue"
    assert "2 unread" in d["reason"] and "mailbox.sh read w1" in d["reason"]


def test_always_prints_one_json_object_outside_a_worker_environment(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("EE_")}
    r = subprocess.run([str(HOOK), "busy"], input="{}", capture_output=True, text=True, env=env)
    assert r.returncode == 0
    assert json.loads(r.stdout) == {}
    assert not (tmp_path / "states").exists()


def test_garbage_payload_is_tolerated(tmp_path):
    out = run_hook("busy", {}, tmp_path)  # json.dumps({}) is valid but empty
    assert json.loads(out) == {}
    assert state_of(tmp_path)["state"] == "busy"
