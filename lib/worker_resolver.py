"""Merge tmux scan results with workers-runtime.env metadata.

Registry entries store `session:window` targets, but scanner reports
`session:window.pane`. The resolver matches by `session:window` prefix.
"""

from __future__ import annotations

import re
from pathlib import Path

_REGISTRY_LINE = re.compile(r"^WORKER_(\w+)_(NAME|TARGET|DIR|ROLE|EXTERNAL|KIND)=(.*)$", re.M)


def parse_registry_text(text: str) -> dict:
    """Parse workers-runtime.env text into {target: meta_dict}."""
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
        result[target] = {
            "name": info.get("NAME", sn),
            "role": info.get("ROLE", ""),
            "cwd": info.get("DIR", ""),
            "external": info.get("EXTERNAL") == "1",
            # Agent flavor (claude|codex). Absent in legacy entries ⇒ claude.
            "agent_kind": info.get("KIND", "claude"),
        }
    return result


def parse_registry_file(path: Path) -> dict:
    if not path.exists():
        return {}
    return parse_registry_text(path.read_text())


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
    """
    target = p["target"]
    candidates = [target, _target_to_window_key(target)]
    window_name = p.get("window_name", "")
    if window_name:
        session = target.split(":", 1)[0]
        candidates.append(f"{session}:{window_name}")
    return candidates


def resolve_workers(scanned: list[dict], registry: dict) -> list[dict]:
    """Annotate scanned pane dicts with name/role/kind from registry."""
    out: list[dict] = []
    for p in scanned:
        meta = None
        for key in _candidate_keys(p):
            meta = registry.get(key)
            if meta:
                break
        if meta:
            out.append(
                {
                    **p,
                    "name": meta["name"],
                    "role": meta["role"],
                    "cwd": meta.get("cwd") or p["cwd"],
                    "external": meta.get("external", False),
                    "kind": "registered",
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
                }
            )
    return out
