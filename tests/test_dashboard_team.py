"""Team-assignment regression: a worker NAME duplicated across teams (e.g. a
stale leftover entry) must not hijack the team. Resolution is by tmux TARGET."""

import pytest

import dashboard
from dashboard import _all_state_dirs, _registered_team_for_worker


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


@pytest.fixture
def hub_at(tmp_path, monkeypatch):
    """Point the hub's globals at a throwaway state root."""

    def _setup(own_team="mine"):
        monkeypatch.setattr(dashboard, "STATE_ROOT", tmp_path)
        monkeypatch.setattr(dashboard, "STATE_DIR", tmp_path / own_team)
        monkeypatch.setattr(dashboard, "SESSION_NAME", own_team)
        return tmp_path

    return _setup


def _make_team(root, *parts):
    d = root.joinpath(*parts)
    d.mkdir(parents=True, exist_ok=True)
    (d / "workers-runtime.env").write_text("WORKER_a_TARGET=x:1\n")
    return d


def test_hub_sees_sibling_teams_not_just_its_own(hub_at):
    """The hub serves the whole host, so a team it wasn't launched from still
    gets named — previously only STATE_DIR and its sub-dirs were discovered."""
    root = hub_at("mine")
    _make_team(root, "mine")
    _make_team(root, "other")
    _make_team(root, "third")
    (root / "no-registry").mkdir()
    (root / "agent.config").write_text("HUB_URL=http://x\n")

    assert dict(_all_state_dirs()) == {
        root / "mine": "mine",
        root / "other": "other",
        root / "third": "third",
    }


def test_nested_worktree_team_keeps_composite_name(hub_at):
    root = hub_at("mine")
    _make_team(root, "mine")
    _make_team(root, "other")
    _make_team(root, "other", "feature-x")

    found = dict(_all_state_dirs())
    assert found[root / "other" / "feature-x"] == "other-feature-x"


def test_own_state_dir_included_without_registry(hub_at):
    """A hub started before anything spawned still reports its own team."""
    root = hub_at("mine")
    _make_team(root, "other")

    found = _all_state_dirs()
    assert (root / "mine", "mine") in found
    assert len(found) == 2


def test_own_team_listed_once_under_session_name(hub_at):
    """STATE_DIR is seeded first; the root sweep must not add it again, and the
    --session-name the hub was given wins over the directory name."""
    root = hub_at("mine")
    _make_team(root, "mine")

    found = _all_state_dirs()
    assert found.count((root / "mine", "mine")) == 1
    assert len(found) == 1
