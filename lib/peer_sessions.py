"""Recover Claude Code peer-messaging names for scanned tmux panes.

Claude Code (v2.1.224+, cross-session messaging) registers every session in
`~/.claude/sessions/<pid>.json`:

    {"pid": 6280, "name": "darkarchon-c3", "nameSource": "derived",
     "tmux": "dark:@0.%0", "sessionId": "...", ...}

`name` is the address other sessions use with SendMessage/ListAgents, and
`tmux` is the pane the session runs in, keyed by tmux's immutable window/pane
ids ("session:@window.%pane"). Joining that against a scanned pane's ids gives
the dashboard a copyable session name for every Claude pane on the host —
registered worker or not — without any hook cooperation from the session.

Host-scoped like the scanner itself: the registry describes this machine's
sessions, and agent.py runs one instance per host.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def sessions_dir() -> Path:
    """Claude Code's session registry directory on this host."""
    cfg = os.environ.get("CLAUDE_CONFIG_DIR")
    base = Path(cfg).expanduser() if cfg else Path.home() / ".claude"
    return base / "sessions"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def read_peer_sessions(directory: Path | None = None) -> dict[str, dict]:
    """Map "session:@window.%pane" → {"peer_name", "session_id"} for live sessions.

    Registry files outlive their session (nothing reaps them on a crash), so a
    file only counts while its recorded pid is alive. The tmux join key on top
    of that makes a recycled-pid collision practically impossible — the recycled
    process would also have to sit in the exact same pane.
    """
    d = directory if directory is not None else sessions_dir()
    out: dict[str, dict] = {}
    try:
        files = list(d.glob("*.json"))
    except OSError:
        return out
    for f in files:
        try:
            rec = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(rec, dict):
            continue
        name, tmux, pid = rec.get("name"), rec.get("tmux"), rec.get("pid")
        if not (isinstance(name, str) and name and isinstance(tmux, str) and tmux):
            continue
        if not (isinstance(pid, int) and _pid_alive(pid)):
            continue
        entry = {"peer_name": name}
        sid = rec.get("sessionId")
        if isinstance(sid, str) and sid:
            entry["session_id"] = sid
        out[tmux] = entry
    return out


def _pane_key(worker: dict) -> str:
    """The join key a scanned worker's pane would have in the registry."""
    window_id = worker.get("window_id") or ""
    pane_id = worker.get("pane_id") or ""
    target = worker.get("target") or ""
    session = target.split(":", 1)[0]
    if not (session and window_id and pane_id):
        return ""
    return f"{session}:{window_id}.{pane_id}"


def annotate_workers_with_peer_names(
    workers: list[dict], registry: dict[str, dict] | None = None
) -> list[dict]:
    """Stamp `peer_name` onto every scanned worker whose pane hosts a live
    Claude Code session. Panes with no match (codex, gemini, dead sessions,
    pre-messaging Claude builds) pass through untouched — the field is simply
    absent, and the UI offers no copy for it.
    """
    reg = registry if registry is not None else read_peer_sessions()
    if not reg:
        return workers
    out: list[dict] = []
    for w in workers:
        hit = reg.get(_pane_key(w))
        out.append({**w, "peer_name": hit["peer_name"]} if hit else w)
    return out
