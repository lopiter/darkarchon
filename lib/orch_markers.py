"""orchestrator.txt markers — the one reader for both the hub and the agent.

spawn-worker.sh / invite-worker.sh record the calling pane in a team's
`orchestrator.txt`, which is how a pane that dispatched into a team gets the
orchestrator badge without any historical dispatch to infer from.

The file names a tmux pane, and tmux identifiers are per-server, i.e. per
machine — so only the host that owns the state dir can resolve one. The hub
reads its own; every other host's agent reads its own and reports the result.
Both go through this module: the hub and the resolver previously kept separate
copies of a window-id lookup, and the copies drifted until a stale id in one
of them regrouped an unrelated pane on another machine.
"""

from __future__ import annotations

from pathlib import Path

MARKER_FILENAME = "orchestrator.txt"


def parse_marker_line(line: str) -> tuple[str, str]:
    """Split one orchestrator.txt line into (pane_key, window_id).

    Legacy: `session:win.pane`. New: `session:win.pane @14` (window_id from
    tmux, always starts with @). One line either way.
    """
    raw = (line or "").strip()
    if not raw:
        return "", ""
    pane, sep, tail = raw.rpartition(" ")
    if sep and tail.startswith("@") and pane:
        return pane.strip(), tail
    return raw, ""


def read_markers(state_dirs) -> tuple[dict[str, str], dict[str, str]]:
    """Collect markers from `[(state_dir, team_name), ...]`.

    Returns (by_pane, by_window_id), each {key: team_name}. A marker registers
    under exactly one of the two: window id when the line carries it, the pane
    key otherwise. Never both — pane indices are reused after a respawn, so
    keeping the old key alongside would still badge whoever sits at sess:1.1.
    """
    by_pane: dict[str, str] = {}
    by_window_id: dict[str, str] = {}
    for state_dir, team_name in state_dirs:
        marker = Path(state_dir) / MARKER_FILENAME
        if not marker.exists():
            continue
        try:
            text = marker.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if not text:
            continue
        # First non-empty line only — the file is still a single marker.
        line = next((ln for ln in text.splitlines() if ln.strip()), "")
        pane, window_id = parse_marker_line(line)
        if window_id:
            by_window_id[window_id] = team_name
        elif pane:
            by_pane[pane] = team_name
    return by_pane, by_window_id


def marker_team_for(worker: dict, by_pane: dict, by_window_id: dict) -> str:
    """The team whose marker points at this pane, or "".

    Window id first: a new-format marker never registers its pane key, so a
    reused index cannot match it. The pane-key lookup is the fallback for
    legacy lines written before ids were recorded.
    """
    window_id = (worker.get("window_id") or "").strip()
    if window_id and window_id in by_window_id:
        return by_window_id[window_id]
    target = (worker.get("target") or "").strip()
    if target and target in by_pane:
        return by_pane[target]
    return ""
