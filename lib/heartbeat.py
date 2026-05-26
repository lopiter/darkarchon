"""Worker heartbeat reader.

Counterpart to lib/heartbeat-writer.sh. Reads `<state_dir>/heartbeats/
<safe_name>.json` for liveness checks and forces dead state when a
heartbeat goes stale, regardless of what tmux pane scan thinks.

Stale rule: HEARTBEAT_STALE_SEC since last_seen → dead.
Process check: if pid no longer alive (kill -0) → dead.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

HEARTBEAT_STALE_SEC = 15


def _sanitize(name: str) -> str:
    """Same scheme as `safe_name()` in lib/_lib.sh."""
    return "".join(c if c.isalnum() or c == "_" else "_" for c in name)


def read_heartbeat(state_dir: Path, worker_name: str) -> dict | None:
    f = Path(state_dir) / "heartbeats" / f"{_sanitize(worker_name)}.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text())
    except Exception:
        return None


def is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def heartbeat_age_sec(hb: dict | None, now: float | None = None) -> float | None:
    """Seconds since the heartbeat's last_seen_epoch. None if no heartbeat."""
    if not hb or "last_seen_epoch" not in hb:
        return None
    now = now if now is not None else time.time()
    return max(0.0, now - hb["last_seen_epoch"])


def annotate_workers(workers: list[dict], *state_dirs: Path) -> list[dict]:
    """Enrich worker dicts with heartbeat info and force dead state when
    the heartbeat says so. Searches the given state_dirs in order for each
    worker's heartbeat — lets the agent cover both its own team and
    sibling worktree teams. Workers without any heartbeat file are left
    alone (legacy / external-invited workers don't run our wrapper)."""
    now = time.time()
    out: list[dict] = []
    for w in workers:
        name = w.get("name", "")
        hb = None
        for sd in state_dirs:
            hb = read_heartbeat(sd, name)
            if hb is not None:
                break
        age = heartbeat_age_sec(hb, now)
        pid = (hb or {}).get("pid")
        alive = is_pid_alive(pid) if isinstance(pid, int) else None

        enriched = dict(w)
        enriched["heartbeat_age_sec"] = age
        enriched["heartbeat_pid_alive"] = alive
        # Force dead when heartbeat exists and either staled or pid gone.
        if hb is not None:
            if age is not None and age > HEARTBEAT_STALE_SEC:
                enriched["state"] = "dead"
                enriched["detail"] = f"heartbeat stale ({int(age)}s)"
            elif alive is False:
                enriched["state"] = "dead"
                enriched["detail"] = "worker pid gone"
        out.append(enriched)
    return out
