"""Team-assignment regression: a worker NAME duplicated across teams (e.g. a
stale leftover entry) must not hijack the team. Resolution is by tmux TARGET."""

from dashboard import _registered_team_for_worker


def test_target_match_beats_stale_same_name_entry():
    # 'homepage-backend' is registered live in the live-team session (window
    # name) AND left over stale in 'default' (a dead session). The live pane
    # reports a window-INDEX target; it must resolve to live-team, never to
    # the stale default.
    teams_by_target = {
        "live-team:homepage-backend": "live-team",
        "default:homepage-backend": "default",  # stale leftover, different target
    }
    worker = {
        "target": "live-team:2.1",
        "window_name": "homepage-backend",
    }
    assert _registered_team_for_worker(worker, teams_by_target) == "live-team"


def test_direct_window_index_target_match():
    teams_by_target = {"teamA:1": "teamA"}
    worker = {"target": "teamA:1.0", "window_name": "whatever"}
    assert _registered_team_for_worker(worker, teams_by_target) == "teamA"


def test_no_match_returns_none():
    teams_by_target = {"teamA:dev": "teamA"}
    worker = {"target": "teamB:2.1", "window_name": "reviewer"}
    assert _registered_team_for_worker(worker, teams_by_target) is None


def test_empty_target_returns_none():
    assert _registered_team_for_worker({"target": ""}, {"x:y": "t"}) is None
