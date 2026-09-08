"""lib/start-worker-claude.sh — the per-worker hooks settings file it generates.

The launcher execs claude at the end, so the script runs here with a stub
`claude` on PATH; what we assert is the settings file it leaves behind in
$STATE_DIR, which is what actually drives lib/state-hook.sh at runtime.
"""

import json
import os
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = ROOT / "lib" / "start-worker-claude.sh"


def run_launcher(tmp_path: Path, worker: str = "w1", role: str = "worker") -> dict:
    """Run the launcher with a stub claude and return the hooks settings it wrote."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "claude"
    stub.write_text("#!/bin/sh\nexit 0\n")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}"}
    # Files, not pipes: the launcher leaves heartbeat-writer.sh running in the
    # background holding the inherited stdout, so capture_output would block
    # until that child exits rather than when claude does.
    log = tmp_path / "launcher.log"
    with open(log, "w") as out:
        r = subprocess.run(
            [str(LAUNCHER), worker, role, str(ROOT), str(state_dir)],
            stdin=subprocess.DEVNULL, stdout=out, stderr=out, env=env, timeout=30,
        )
    assert r.returncode == 0, log.read_text()
    return json.loads((state_dir / f"hooks-settings-{worker}.json").read_text())


def command_for(cfg: dict, event: str) -> str:
    return cfg["hooks"][event][0]["hooks"][0]["command"]


def test_post_tool_use_reports_busy(tmp_path):
    # The approval that ends a PermissionRequest fires no hook of its own. Running
    # the tool does — and a running tool means the worker is working again, so
    # PostToolUse is what clears the awaiting_permission record mid-turn.
    cfg = run_launcher(tmp_path)
    assert command_for(cfg, "PostToolUse").endswith(" busy")


def test_permission_request_still_reports_awaiting_permission(tmp_path):
    cfg = run_launcher(tmp_path)
    assert command_for(cfg, "PermissionRequest").endswith(" awaiting_permission")
