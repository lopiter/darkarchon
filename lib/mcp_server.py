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


def _mailbox_sh(*args: str) -> subprocess.CompletedProcess:
    """Run lib/mailbox.sh, which owns the mailbox format and delivery rules.

    This tool used to reimplement writing and notifying in Python, and the two
    copies drifted: the Python one never pressed Enter after the trigger, so its
    notification sat unsent on the recipient's prompt line — which the state
    detector then read as `unsent` and dispatch-safe refused to dispatch to.
    Delegating keeps group addressing, read_at stamping and the trigger protocol
    in exactly one place.
    """
    return subprocess.run(
        [str(Path(__file__).resolve().parent / "mailbox.sh"), *args],
        capture_output=True, text=True, timeout=30,
        env={**os.environ, "EE_WORKER_NAME": WORKER_NAME},
    )


@mcp.tool()
def mailbox_send(to: str, body: str) -> str:
    """Send a message to another worker's mailbox and notify them.

    Use to coordinate with peer workers without involving the user. The
    recipient is responsible for draining their mailbox (`mailbox_drain`).

    Args:
        to: Recipient worker name, or a group address to reach several at once:
            @all, @idle, @claude, @codex, @cwd:<dir>. You are never included in
            your own group send.
        body: Message body.

    Returns:
        The message id, or one `to=<worker> id=<id>` line per recipient for a
        group send.
    """
    r = _mailbox_sh("send", to, "--from", WORKER_NAME, body)
    if r.returncode != 0:
        return f"Send failed (exit {r.returncode}): {(r.stderr or '').strip()}"
    return r.stdout.strip() or "sent"


@mcp.tool()
def mailbox_drain() -> str:
    """Read and remove all pending messages in your own mailbox.

    Destructive: drained messages are moved to <self>.drained.jsonl, stamped
    with read_at so an undelivered message can be told from an unread one.
    Returns the raw JSONL content (one message per line); empty string if
    there's nothing pending.
    """
    r = _mailbox_sh("read", WORKER_NAME)
    if r.returncode != 0:
        return f"Drain failed (exit {r.returncode}): {(r.stderr or '').strip()}"
    out = r.stdout.strip()
    return "" if out == "(empty)" else out


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
