"""darkarchon MCP server — stdio, per-worker.

Spawned by start-worker-claude.sh as the worker's MCP server. Exposes the
file-based actions (ask, mailbox send/drain, status) as type-safe MCP
tools so Claude Code doesn't have to remember sh command shapes.

Identity is taken from env (EE_WORKER_NAME, EE_STATE_DIR), exported by
the wrapper. On-disk format matches lib/ask.sh + lib/mailbox.sh, so the
legacy sh commands continue to work alongside the MCP server — the hub
and dashboard read the same files either way.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from mcp.server.fastmcp import FastMCP

WORKER_NAME = os.environ.get("EE_WORKER_NAME", "unknown")
STATE_DIR = Path(
    os.environ.get("EE_STATE_DIR")
    or os.environ.get("STATE_DIR")
    or (Path.home() / ".darkarchon" / "default")
)

mcp = FastMCP("darkarchon")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _gen_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{ts}-{os.urandom(2).hex()}"


def _sanitize_worker_name(name: str) -> str:
    """Same scheme as `safe_name()` in lib/_lib.sh."""
    return "".join(c if c.isalnum() or c == "_" else "_" for c in name)


def _lookup_target(worker_name: str) -> str | None:
    """Find a worker's tmux target by parsing workers-runtime.env. Returns
    None if the recipient isn't registered (the caller should warn)."""
    rt = STATE_DIR / "workers-runtime.env"
    if not rt.exists():
        return None
    var = f"WORKER_{_sanitize_worker_name(worker_name)}_TARGET="
    for raw in rt.read_text().splitlines():
        line = raw.strip()
        if line.startswith(var):
            return line[len(var):].strip("'").strip('"')
    return None


@mcp.tool()
def ask(question: str, context: str = "") -> str:
    """File a question for the orchestrator (the human user) to answer.

    Use whenever a human decision is needed that the role prompt does not
    cover. The question lands in the team's questions queue and shows up
    on the user's dashboard. Don't ask trivial questions; prefer to make
    a reasonable default and proceed.

    Args:
        question: The question itself. Be specific and include any choice
                  space relevant to deciding.
        context: Optional context about what you tried / why you're stuck.

    Returns:
        Question ID.
    """
    qdir = STATE_DIR / "questions"
    qdir.mkdir(parents=True, exist_ok=True)
    qid = _gen_id()
    body = question
    if context:
        body = f"{question}\n\n---\nContext:\n{context}"
    data = {
        "question_id": qid,
        "from_worker": WORKER_NAME,
        "body": body,
        "created_at": _now_iso(),
        "status": "pending",
    }
    (qdir / f"{qid}.json").write_text(json.dumps(data))
    return f"Question filed: {qid}"


@mcp.tool()
def mailbox_send(to: str, body: str) -> str:
    """Send a message to another worker's mailbox and notify them.

    Use to coordinate with peer workers without involving the user. The
    recipient is responsible for draining their mailbox (`mailbox_drain`).

    Args:
        to: Recipient worker name. Must be registered in the team (or
            this call will succeed at writing the file but the notification
            will be skipped).
        body: Message body.

    Returns:
        Message ID + whether the tmux notification was sent.
    """
    mb_dir = STATE_DIR / "mailboxes"
    mb_dir.mkdir(parents=True, exist_ok=True)
    msg_id = f"{int(time.time() * 1e9)}-{os.urandom(2).hex()}"
    data = {
        "message_id": msg_id,
        "from_worker": WORKER_NAME,
        "to_worker": to,
        "body": body,
        "created_at": _now_iso(),
    }
    with (mb_dir / f"{to}.jsonl").open("a") as f:
        f.write(json.dumps(data) + "\n")

    notified = False
    target = _lookup_target(to)
    if target:
        trigger = f"MAILBOX_NOTIFY from={WORKER_NAME} mailbox={to}"
        try:
            subprocess.run(
                ["tmux", "send-keys", "-t", f"={target}", trigger],
                check=False, capture_output=True, timeout=2,
            )
            notified = True
        except Exception:
            pass
    return f"Sent {msg_id} (notified={notified})"


@mcp.tool()
def mailbox_drain() -> str:
    """Read and remove all pending messages in your own mailbox.

    Destructive: drained messages are moved to <self>.drained.jsonl so
    they aren't seen twice. Returns the raw JSONL content (one message
    per line); empty string if there's nothing pending.
    """
    mailbox = STATE_DIR / "mailboxes" / f"{WORKER_NAME}.jsonl"
    if not mailbox.exists() or mailbox.stat().st_size == 0:
        return ""
    drained = STATE_DIR / "mailboxes" / f"{WORKER_NAME}.drained.jsonl"
    content = mailbox.read_text()
    with drained.open("a") as f:
        f.write(content)
    mailbox.unlink()
    return content


@mcp.tool()
def status_get() -> str:
    """Brief status: own worker name, state dir, pending mailbox count,
    last 3 tasks. Useful for self-introspection mid-prompt."""
    mailbox = STATE_DIR / "mailboxes" / f"{WORKER_NAME}.jsonl"
    pending = 0
    if mailbox.exists():
        try:
            pending = sum(1 for _ in mailbox.open())
        except Exception:
            pass

    recent: list[dict] = []
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from lib.task_store import TaskStore  # noqa: E402
        store = TaskStore(STATE_DIR / "tasks.db")
        recent = [
            {"id": t["id"], "status": t["status"], "dispatched_at": t["dispatched_at"]}
            for t in store.list(worker=WORKER_NAME, limit=3)
        ]
    except Exception:
        pass

    return json.dumps(
        {
            "worker": WORKER_NAME,
            "state_dir": str(STATE_DIR),
            "pending_mailbox": pending,
            "recent_tasks": recent,
        },
        indent=2,
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
