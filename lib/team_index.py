"""Team discovery and activity aging, shared by the hub and the teams CLI.

A "team" is a state directory holding workers-runtime.env. Nothing records when
a team stopped being used, so activity is reconstructed from the traces its own
tooling already leaves on disk. `teams.sh` reads this without a running hub, and
`dashboard.py` layers live host reports on top — both must agree, hence one
module rather than two implementations.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from .worker_resolver import parse_registry_file

REGISTRY_FILENAME = "workers-runtime.env"

# Default aging thresholds in days; the hub and teams.sh both override these
# from config.env (TEAM_DORMANT_DAYS / TEAM_STALE_DAYS).
DEFAULT_DORMANT_DAYS = 7
DEFAULT_STALE_DAYS = 30

# Tiers, ordered from most to least active. 'empty' is separate from 'stale':
# a team with no registry entries at all was torn down cleanly, not abandoned.
TIERS = ("live", "recent", "dormant", "stale", "empty")


def _iso_to_epoch(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _epoch_to_iso(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def discover_teams(root: Path, order: str = "name") -> list[tuple[Path, str]]:
    """Every team state dir under `root`, as (path, team_name).

    Two layouts coexist and both must be found:
      <root>/<team>/              flat team (config.env's STATE_DIR)
      <root>/<team>/<sub>/        worktree team nested under it

    Team names mirror the SESSION_NAME each team's scripts resolve — config.env
    sets STATE_DIR=<root>/<SESSION_NAME>, so a flat dir's name is its team name
    and a nested one is '<team>-<sub>'.

    order='name' sorts alphabetically; order='mtime' sorts by registry mtime
    oldest-first, which callers merging target-keyed entries rely on so the
    freshest registration wins on collision.
    """
    if not root.is_dir():
        return []
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return []
    found: list[tuple[Path, str]] = []
    for team in entries:
        if not team.is_dir():
            continue
        if (team / REGISTRY_FILENAME).exists():
            found.append((team, team.name))
        try:
            subs = sorted(team.iterdir())
        except OSError:
            continue
        for sub in subs:
            if sub.is_dir() and (sub / REGISTRY_FILENAME).exists():
                found.append((sub, f"{team.name}-{sub.name}"))
    if order == "mtime":
        found.sort(key=lambda pair: _mtime(pair[0] / REGISTRY_FILENAME) or 0.0)
    return found


def _newest_heartbeat(state_dir: Path) -> float | None:
    newest = None
    hb_dir = state_dir / "heartbeats"
    if not hb_dir.is_dir():
        return None
    for f in hb_dir.glob("*.json"):
        try:
            epoch = json.loads(f.read_text()).get("last_seen_epoch")
        except (OSError, ValueError):
            continue
        if isinstance(epoch, (int, float)) and (newest is None or epoch > newest):
            newest = float(epoch)
    return newest


def _newest_task(state_dir: Path) -> float | None:
    """Newest dispatch timestamp, or None when the team never ran one.

    Skips a missing tasks.db rather than opening one, because TaskStore creates
    the file (and its schema) on construction — asking a dormant team when it
    last worked must not resurrect it with an empty database.
    """
    if not (state_dir / "tasks.db").exists():
        return None
    from .task_store import TaskStore

    try:
        return _iso_to_epoch(TaskStore(state_dir / "tasks.db").last_activity_any())
    except Exception:
        return None


def _newest_mailbox(state_dir: Path) -> float | None:
    """Newest mailbox file mtime.

    File mtime rather than the messages' own timestamps: a drain rewrites the
    file, and being drained is itself activity worth counting. Reading every
    line of every jsonl across all teams would cost far more for a weaker
    signal.
    """
    mb_dir = state_dir / "mailboxes"
    if not mb_dir.is_dir():
        return None
    times = [t for t in (_mtime(f) for f in mb_dir.glob("*.jsonl")) if t is not None]
    return max(times) if times else None


def _dir_size_bytes(state_dir: Path) -> int:
    total = 0
    try:
        for f in state_dir.rglob("*"):
            try:
                if f.is_file():
                    total += f.stat().st_size
            except OSError:
                continue
    except OSError:
        pass
    return total


def team_activity(state_dir: Path) -> dict:
    """Last-activity epoch for one team plus which signal produced it.

    Four independent traces, maxed. Reporting the winning source matters as much
    as the timestamp: "40 days since any dispatch" and "spawned yesterday, never
    given work" are different situations that a bare max collapses together.
    """
    signals = {
        "dispatch": _newest_task(state_dir),
        "heartbeat": _newest_heartbeat(state_dir),
        "registry": _mtime(state_dir / REGISTRY_FILENAME),
        "mailbox": _newest_mailbox(state_dir),
    }
    present = {k: v for k, v in signals.items() if v is not None}
    if not present:
        return {"last_activity_epoch": None, "last_activity_source": None, "signals": signals}
    source = max(present, key=lambda k: present[k])
    return {
        "last_activity_epoch": present[source],
        "last_activity_source": source,
        "signals": signals,
    }


def classify(
    idle_seconds: float | None,
    *,
    is_live: bool,
    worker_count: int,
    dormant_days: int = DEFAULT_DORMANT_DAYS,
    stale_days: int = DEFAULT_STALE_DAYS,
) -> str:
    if is_live:
        return "live"
    if worker_count == 0:
        return "empty"
    if idle_seconds is None:
        return "stale"
    if idle_seconds < dormant_days * 86400:
        return "recent"
    if idle_seconds < stale_days * 86400:
        return "dormant"
    return "stale"


def build_index(
    root: Path,
    *,
    live_teams: set | None = None,
    dormant_days: int = DEFAULT_DORMANT_DAYS,
    stale_days: int = DEFAULT_STALE_DAYS,
    teams: list | None = None,
    now: float | None = None,
) -> list[dict]:
    """One row per team, most recently active first.

    `live_teams` names teams a host is currently reporting workers for; the hub
    passes it, teams.sh leaves it empty and lets a fresh heartbeat stand in.
    `teams` overrides discovery so the hub can reuse its own naming for the
    directory it was launched from.
    """
    now = time.time() if now is None else now
    live_teams = live_teams or set()
    rows = []
    for state_dir, name in teams if teams is not None else discover_teams(root):
        activity = team_activity(state_dir)
        epoch = activity["last_activity_epoch"]
        idle = None if epoch is None else max(0.0, now - epoch)
        registry = parse_registry_file(state_dir / REGISTRY_FILENAME)
        # A heartbeat inside its own staleness window means a worker is running
        # right now, which makes the team live even with no hub to report it.
        hb = activity["signals"]["heartbeat"]
        is_live = name in live_teams or (hb is not None and now - hb < 60)
        rows.append(
            {
                "name": name,
                "state_dir": str(state_dir),
                "workers": len(registry),
                "last_activity_at": _epoch_to_iso(epoch),
                "last_activity_source": activity["last_activity_source"],
                "idle_seconds": None if idle is None else int(idle),
                "tier": classify(
                    idle,
                    is_live=is_live,
                    worker_count=len(registry),
                    dormant_days=dormant_days,
                    stale_days=stale_days,
                ),
                "size_bytes": _dir_size_bytes(state_dir),
            }
        )
    rows.sort(key=lambda r: (r["idle_seconds"] is None, r["idle_seconds"] or 0))
    return rows
