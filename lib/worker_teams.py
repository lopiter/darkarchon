"""Host-local team facts for each scanned pane.

Which team owns a pane, and whether a team's orchestrator.txt points at it,
are questions only the machine holding those state dirs can answer — tmux
targets and window ids are per-server namespaces, and a state dir is not
readable across hosts at all. So each host's agent answers them for its own
panes and reports the answers; the hub reads its own disk for its own panes
and takes the reported values for everyone else's.

Reporting the facts rather than the verdict keeps the grouping policy in one
place (the hub), which owns the precedence rules and the tiering thresholds.
"""

from __future__ import annotations

from pathlib import Path

from lib.orch_markers import marker_team_for, read_markers


def annotate_workers_with_team_facts(workers: list[dict], state_dirs) -> list[dict]:
    """Stamp `owner_team` and `marker_team` on every worker.

    `state_dirs` is `[(path, team_name), ...]` — the host's own teams.

    owner_team: the team whose registry this pane resolved to. resolve_workers
    already recorded that directory on the worker, so this is a lookup, not a
    second match — a re-match could disagree with the name the pane was given.
    Empty for a discovered pane, which by definition matched no registry.

    marker_team: the team whose orchestrator.txt names this pane. Empty for
    the overwhelming majority; a host has one marker per team at most.
    """
    by_team = {str(Path(d)): name for d, name in state_dirs}
    by_pane, by_window_id = read_markers(state_dirs)
    for w in workers:
        own = (w.get("state_dir") or "").strip()
        w["owner_team"] = by_team.get(own, "") if own else ""
        w["marker_team"] = marker_team_for(w, by_pane, by_window_id)
    return workers
