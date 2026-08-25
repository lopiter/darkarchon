"""Merge tmux scan results with workers-runtime.env metadata.

Registry entries store `session:window` targets, but scanner reports
`session:window.pane`. The resolver matches by `session:window` prefix.
"""

from __future__ import annotations

import re
from pathlib import Path

_REGISTRY_LINE = re.compile(
    r"^WORKER_(\w+)_(NAME|TARGET|DIR|ROLE|EXTERNAL|KIND|SPAWNED_BY|WINDOW_ID|SESSION)=(.*)$", re.M
)


def parse_registry_text(text: str) -> dict:
    """Parse workers-runtime.env text into {key: meta_dict}.

    Keyed by tmux TARGET (`session:window`), and additionally by tmux window id
    (`@5`) when the entry records one. The two key shapes can't collide — a
    window id always starts with '@' — so one flat dict serves both lookups.
    Entries written before WINDOW_ID existed simply have the one key.
    """
    bag: dict[str, dict[str, str]] = {}
    for m in _REGISTRY_LINE.finditer(text):
        sn, key, val = m.groups()
        val = val.strip().strip("'").strip('"')
        bag.setdefault(sn, {})[key] = val

    result: dict[str, dict] = {}
    for sn, info in bag.items():
        target = info.get("TARGET")
        if not target:
            continue
        meta = {
            "name": info.get("NAME", sn),
            "role": info.get("ROLE", ""),
            "cwd": info.get("DIR", ""),
            "external": info.get("EXTERNAL") == "1",
            # Agent flavor (claude|codex). Absent in legacy entries ⇒ claude.
            "agent_kind": info.get("KIND", "claude"),
            # Worker name of whoever spawned this one (lineage). Absent for
            # legacy entries, invited panes, and human-spawned workers.
            "spawned_by": info.get("SPAWNED_BY", ""),
            # Dedicated tmux session (spawn --session). Empty when the window
            # lives in the team's own session. The hub uses a non-empty value
            # as display grouping so a fleet-registered orchestrator is not
            # drawn inside the fleet just because that is where its registry
            # row lives.
            "session": info.get("SESSION", ""),
            # Kept so a window-id match can be checked against the session it
            # was registered in (see `_window_id_match`).
            "target": target,
            "window_id": info.get("WINDOW_ID", ""),
        }
        result[target] = meta
        if meta["window_id"]:
            result[meta["window_id"]] = meta
    return result


def parse_registry_file(path: Path) -> dict:
    if not path.exists():
        return {}
    # Decode tolerantly: a registry may contain a mojibake byte in a free-form
    # ROLE value (e.g. a mangled em-dash). The keys we parse are ASCII, so
    # replacing undecodable bytes preserves every TARGET/NAME/KIND while keeping
    # one corrupt team file from crashing the whole multi-team merge.
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    entries = parse_registry_text(text)
    # Stamp the owning team's state dir on every entry. Worker names repeat
    # across teams — nine of this machine's teams have a `homepage-backend` —
    # so anything looking up that worker's heartbeat or hook state by name has
    # to know which team's directory to look in.
    for meta in entries.values():
        meta["state_dir"] = str(path.parent)
    return entries


def read_scoped(reader, worker: dict, name: str, state_dirs):
    """Read a per-worker file from the team that actually owns this worker.

    Heartbeat and hook-state files are named after the worker, and worker names
    are only unique within a team. Searching every team's directory by name
    therefore picks up strangers: a worker busy in one team was reported as
    awaiting input because a same-named worker in a team last touched 17 days
    earlier had a stale `awaiting_user` file, and the search happened to reach
    it first.

    Registered workers carry the state dir they were registered in, so their
    lookup is exact. Anything unregistered has no team to scope to and keeps
    the old search — those are discovered panes, whose names are tmux targets
    that match no file anyway.
    """
    own = worker.get("state_dir")
    if own:
        return reader(Path(own), name)
    for sd in state_dirs:
        found = reader(sd, name)
        if found is not None:
            return found
    return None


def _target_to_window_key(target: str) -> str:
    """Strip the `.pane` suffix from 'session:window.pane' to get 'session:window'."""
    if "." in target.split(":", 1)[-1]:
        return target.rsplit(".", 1)[0]
    return target


def _candidate_keys(p: dict) -> list[str]:
    """Generate registry key candidates from a scanned pane dict.

    Registry stores `session:window-name` (since spawn-worker.sh builds the
    target from $NAME, the user-provided worker name). But tmux's
    `pane_current_command` format returns `session:window-index.pane-index`.
    Try multiple shapes so name-based and index-based registries both match.

    Window id is handled separately (`_window_id_match`) because it needs a
    guard the plain key lookups don't.
    """
    target = p["target"]
    candidates = [target, _target_to_window_key(target)]
    window_name = p.get("window_name", "")
    if window_name:
        session = target.split(":", 1)[0]
        candidates.append(f"{session}:{window_name}")
    return candidates


def _window_id_match(p: dict, registry: dict) -> dict | None:
    """Registry entry for this pane's tmux window id, if it is trustworthy.

    A window id is immutable for the window's lifetime, which makes it the only
    key that survives a rename — the failure this exists to fix, since both
    other key shapes are derived from the window's current name or index.

    It is not immortal, though: ids restart from @0 when the tmux server does,
    so a stale entry could collide with an unrelated new window. Requiring the
    session to match too costs nothing and keeps that collision no more likely
    than the name-based matching this sits in front of.
    """
    window_id = p.get("window_id", "")
    if not window_id:
        return None
    meta = registry.get(window_id)
    if not meta:
        return None
    registered_session = (meta.get("target") or "").split(":", 1)[0]
    if registered_session and registered_session != p["target"].split(":", 1)[0]:
        return None
    return meta


def resolve_workers(scanned: list[dict], registry: dict) -> list[dict]:
    """Annotate scanned pane dicts with name/role/kind from registry."""
    out: list[dict] = []
    for p in scanned:
        meta = _window_id_match(p, registry)
        for key in _candidate_keys(p):
            if meta:
                break
            meta = registry.get(key)
        if meta:
            out.append(
                {
                    **p,
                    "name": meta["name"],
                    "role": meta["role"],
                    "cwd": meta.get("cwd") or p["cwd"],
                    "external": meta.get("external", False),
                    "kind": "registered",
                    "spawned_by": meta.get("spawned_by", ""),
                    "session": meta.get("session", ""),
                    # Which team registered this worker — the only directory
                    # whose heartbeat/hook files describe THIS pane.
                    "state_dir": meta.get("state_dir", ""),
                }
            )
        else:
            out.append(
                {
                    **p,
                    "name": p["target"],
                    "role": "",
                    "external": False,
                    "kind": "discovered",
                    "spawned_by": "",
                    "session": "",
                }
            )
    return out
