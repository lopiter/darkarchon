"""Host-local team facts: only the machine holding a state dir can resolve
which team owns a pane or which orchestrator.txt names it."""

import os

from lib.orch_markers import marker_team_for, orch_team_for, parse_marker_line, read_markers
from lib.worker_teams import annotate_workers_with_team_facts


def _team(root, name, marker=None):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "workers-runtime.env").write_text("WORKER_x_TARGET=x:1\n")
    if marker:
        (d / "orchestrator.txt").write_text(marker + "\n")
    return d


def test_owner_team_comes_from_the_registry_that_matched(tmp_path):
    fleet = _team(tmp_path, "fleet")
    other = _team(tmp_path, "other")
    workers = [
        {"target": "fleet:2.1", "state_dir": str(fleet)},
        {"target": "other:1.1", "state_dir": str(other)},
    ]
    annotate_workers_with_team_facts(workers, [(fleet, "fleet"), (other, "other")])
    assert workers[0]["owner_team"] == "fleet"
    assert workers[1]["owner_team"] == "other"


def test_discovered_pane_has_no_owner_team(tmp_path):
    fleet = _team(tmp_path, "fleet")
    workers = [{"target": "6:1.1", "state_dir": ""}]
    annotate_workers_with_team_facts(workers, [(fleet, "fleet")])
    assert workers[0]["owner_team"] == ""


def test_marker_team_matches_by_window_id(tmp_path):
    fleet = _team(tmp_path, "fleet", marker="fleet:1.1 @14")
    workers = [{"target": "fleet:9.1", "window_id": "@14", "state_dir": str(fleet)}]
    annotate_workers_with_team_facts(workers, [(fleet, "fleet")])
    # Window renamed and reindexed; the id still names the orchestrator.
    assert workers[0]["marker_team"] == "fleet"


def test_new_marker_does_not_match_a_reused_pane_index(tmp_path):
    fleet = _team(tmp_path, "fleet", marker="fleet:1.1 @14")
    workers = [{"target": "fleet:1.1", "window_id": "@99", "state_dir": str(fleet)}]
    annotate_workers_with_team_facts(workers, [(fleet, "fleet")])
    assert workers[0]["marker_team"] == ""


def test_legacy_marker_still_matches_its_pane_key(tmp_path):
    fleet = _team(tmp_path, "fleet", marker="fleet:1.1")
    workers = [{"target": "fleet:1.1", "window_id": "", "state_dir": str(fleet)}]
    annotate_workers_with_team_facts(workers, [(fleet, "fleet")])
    assert workers[0]["marker_team"] == "fleet"


def test_unmarked_pane_gets_empty_marker_team(tmp_path):
    fleet = _team(tmp_path, "fleet")
    workers = [{"target": "fleet:1.1", "window_id": "@1", "state_dir": str(fleet)}]
    annotate_workers_with_team_facts(workers, [(fleet, "fleet")])
    assert workers[0]["marker_team"] == ""


def test_parse_marker_line_shapes():
    assert parse_marker_line("dark:1.1") == ("dark:1.1", "")
    assert parse_marker_line("3hour:1.1 @14") == ("3hour:1.1", "@14")
    assert parse_marker_line("  hermes:1.1  @9 \n") == ("hermes:1.1", "@9")
    assert parse_marker_line("") == ("", "")


def test_marker_registers_under_one_key_only(tmp_path):
    """A window-id line must not also claim its pane key — indices get reused."""
    fleet = _team(tmp_path, "fleet", marker="fleet:1.1 @14")
    by_pane, by_wid = read_markers([(fleet, "fleet")])
    assert by_wid == {("fleet", "@14"): "fleet"}
    assert by_pane == {}


def test_empty_and_missing_markers_are_skipped(tmp_path):
    blank = _team(tmp_path, "blank", marker="   ")
    none = _team(tmp_path, "none")
    by_pane, by_wid = read_markers([(blank, "blank"), (none, "none")])
    assert by_pane == {} and by_wid == {}


def test_marker_team_for_prefers_window_id_over_pane_key():
    by_pane = {"a:1.1": "legacy"}
    by_wid = {("a", "@5"): "current"}
    w = {"target": "a:1.1", "window_id": "@5"}
    assert marker_team_for(w, by_pane, by_wid) == "current"


def _age(path, mtime):
    os.utime(path / "orchestrator.txt", (mtime, mtime))


def test_window_id_marker_from_another_session_does_not_match(tmp_path):
    """tmux restarts ids at @0, so `dark:1.1 @2` written before a restart
    names whatever window now holds @2 — here an unrelated insu-terms pane.
    Same session guard worker_resolver._window_id_match applies."""
    dark = _team(tmp_path, "dark", marker="dark:1.1 @2")
    workers = [{"target": "insu-terms:2.1", "window_id": "@2", "state_dir": ""}]
    annotate_workers_with_team_facts(workers, [(dark, "dark")])
    assert workers[0]["marker_team"] == ""
    assert workers[0]["orch_team"] == ""


def test_marker_older_than_tmux_server_is_ignored(tmp_path):
    """A marker written before the tmux server started cannot name a live
    window: every window it knew died with the old server."""
    old = _team(tmp_path, "cto-old", marker="cto-orch:1.1 @3")
    _age(old, 1000)
    workers = [{"target": "cto-orch:1.1", "window_id": "@3", "state_dir": ""}]
    annotate_workers_with_team_facts(workers, [(old, "cto-old")], since=2000)
    assert workers[0]["marker_team"] == ""
    assert workers[0]["orch_team"] == ""


def test_newest_marker_wins_when_window_ids_collide(tmp_path):
    """cto-orch hosts one window per run, and team dirs outlive their runs.
    Directory order must not decide: the newest marker names the live run."""
    a = _team(tmp_path, "cto-aaa", marker="cto-orch:1.1 @144")
    z = _team(tmp_path, "cto-zzz", marker="cto-orch:1.1 @144")
    _age(a, 3000)
    _age(z, 2500)
    _, by_wid = read_markers([(z, "cto-zzz"), (a, "cto-aaa")])
    assert by_wid == {("cto-orch", "@144"): "cto-aaa"}
    _age(z, 3500)
    _, by_wid = read_markers([(z, "cto-zzz"), (a, "cto-aaa")])
    assert by_wid == {("cto-orch", "@144"): "cto-zzz"}


def test_orch_team_comes_from_window_id_markers_only(tmp_path):
    """Legacy pane-key lines still badge, but they cannot regroup a pane:
    pane indices are reused, so only a window-id line is grouping-grade."""
    legacy = _team(tmp_path, "legacy", marker="fleet:1.1")
    workers = [{"target": "fleet:1.1", "window_id": "@9", "state_dir": ""}]
    annotate_workers_with_team_facts(workers, [(legacy, "legacy")])
    assert workers[0]["marker_team"] == "legacy"
    assert workers[0]["orch_team"] == ""


def test_orch_team_for_matches_session_and_window_id():
    by_wid = {("cto-orch", "@144"): "cto-insu-group-env"}
    assert orch_team_for({"target": "cto-orch:1.1", "window_id": "@144"}, by_wid) == "cto-insu-group-env"
    assert orch_team_for({"target": "other:1.1", "window_id": "@144"}, by_wid) == ""
    assert orch_team_for({"target": "cto-orch:1.1", "window_id": ""}, by_wid) == ""
