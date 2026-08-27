"""Host-local team facts: only the machine holding a state dir can resolve
which team owns a pane or which orchestrator.txt names it."""

from lib.orch_markers import marker_team_for, parse_marker_line, read_markers
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
    assert by_wid == {"@14": "fleet"}
    assert by_pane == {}


def test_empty_and_missing_markers_are_skipped(tmp_path):
    blank = _team(tmp_path, "blank", marker="   ")
    none = _team(tmp_path, "none")
    by_pane, by_wid = read_markers([(blank, "blank"), (none, "none")])
    assert by_pane == {} and by_wid == {}


def test_marker_team_for_prefers_window_id_over_pane_key():
    by_pane = {"a:1.1": "legacy"}
    by_wid = {"@5": "current"}
    w = {"target": "a:1.1", "window_id": "@5"}
    assert marker_team_for(w, by_pane, by_wid) == "current"
