"""Team namespacing in the hermes darkarchon-orchestrators plugin.

Every hire names the team it joins; teams are plain darkarchon namespaces
(~/.darkarchon/<team>/) and several coexist, so employee lookup has to span
all of them. These tests cover the pure bookkeeping — nothing here shells
out to tmux or the darkarchon scripts.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

PLUGIN = (Path(__file__).resolve().parents[1] / "hermes-plugin"
          / "darkarchon-orchestrators" / "orchestrators.py")


@pytest.fixture
def orch(tmp_path, monkeypatch):
    """Fresh module instance with HOME and HERMES_HOME pointed at tmp_path."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    monkeypatch.delenv("HERMES_ORCH_TEAM", raising=False)
    spec = importlib.util.spec_from_file_location("orch_under_test", PLUGIN)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["orch_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def register(orch, team, *names):
    """Write a darkarchon runtime registry for `team` holding `names`."""
    d = Path.home() / ".darkarchon" / team
    d.mkdir(parents=True, exist_ok=True)
    lines = []
    for n in names:
        sn = n.replace("-", "_")
        lines += [f"WORKER_{sn}_NAME={n}", f"WORKER_{sn}_TARGET={n}:{n}"]
    (d / "workers-runtime.env").write_text("\n".join(lines) + "\n")
    orch.remember_team(team)


# ── hiring must name a team ────────────────────────────────────────────────

def test_spawn_without_team_asks_the_user(orch, tmp_path):
    out = orch.spawn("backend", str(tmp_path))
    assert out["ok"] is False
    assert "ASKING THE USER" in out["error"]
    assert "spawn" in out["error"]


def test_invite_without_team_asks_the_user(orch):
    out = orch.invite("helper", "main:1")
    assert out["ok"] is False
    assert "ASKING THE USER" in out["error"]


def test_ask_lists_existing_teams(orch, tmp_path):
    register(orch, "api", "backend")
    out = orch.spawn("docs", str(tmp_path))
    assert "api" in out["error"]


def test_spawn_rejects_invalid_team(orch, tmp_path):
    out = orch.spawn("backend", str(tmp_path), team="bad name")
    assert out["ok"] is False and "invalid team name" in out["error"]


def test_spawn_refuses_name_already_hired_elsewhere(orch, tmp_path):
    register(orch, "api", "backend")
    out = orch.spawn("backend", str(tmp_path), team="docs")
    assert out["ok"] is False
    assert "already registered in team 'api'" in out["error"]


# ── team bookkeeping ───────────────────────────────────────────────────────

def test_teams_persist_and_track_the_latest(orch):
    orch.remember_team("api")
    orch.remember_team("docs")
    assert orch.known_teams() == ["api", "docs"]
    assert orch.current_team() == "docs"


def test_env_pins_the_team(orch, monkeypatch):
    orch.remember_team("api")
    monkeypatch.setenv("HERMES_ORCH_TEAM", "pinned")
    assert orch.current_team() == "pinned"
    assert "pinned" in orch.known_teams()


def legacy_state(tmp_path, team):
    """A state file from before teams existed: one team, no "teams" list."""
    (tmp_path / ".hermes").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".hermes" / "darkarchon-orchestrators.json").write_text(
        '{"team": "%s"}' % team)


def test_legacy_single_fleet_state_still_loads(orch, tmp_path):
    legacy_state(tmp_path, "aff")
    assert orch.current_team() == "aff"
    assert orch.known_teams() == ["aff"]


def test_hiring_into_a_new_team_keeps_the_legacy_one(orch, tmp_path):
    """The legacy team lives only in "team"; adding a second must not drop it
    (that would hide its whole roster from list/dispatch)."""
    legacy_state(tmp_path, "aff")
    orch.remember_team("voc")
    assert orch.known_teams() == ["aff", "voc"]
    assert orch.current_team() == "voc"


def test_set_team_does_not_hide_other_teams(orch):
    register(orch, "api", "backend")
    out = orch.set_team("docs")
    assert out["ok"] is True and out["team"] == "docs"
    assert [n for n, _ in orch._all_employees()] == ["backend"]


# ── lookup spans every team ────────────────────────────────────────────────

def test_employees_and_teams_are_listed_together(orch):
    register(orch, "api", "backend")
    register(orch, "docs", "writer")
    assert orch._all_employees() == [("backend", "api"), ("writer", "docs")]
    assert orch._team_of("writer") == "docs"
    assert orch._team_of("nobody") is None


def test_resolve_name_finds_a_prefix_in_another_team(orch):
    register(orch, "api", "backend")
    register(orch, "docs", "writer")
    assert orch._resolve_name("wri") == ("writer", "docs", [])
    assert orch._resolve_name("backend") == ("backend", "api", [])


def test_resolve_name_reports_ambiguity_across_teams(orch):
    register(orch, "api", "worker-a")
    register(orch, "docs", "worker-b")
    name, team, cands = orch._resolve_name("worker")
    assert (name, team) == (None, None)
    assert cands == ["worker-a", "worker-b"]


def test_teams_action_reports_rosters(orch):
    register(orch, "api", "backend")
    register(orch, "docs", "writer")
    data = orch.teams()
    assert {t["team"]: t["employees"] for t in data["teams"]} == {
        "api": ["backend"], "docs": ["writer"]}
    assert [t["team"] for t in data["teams"] if t["current"]] == ["docs"]


# ── run records carry their team ───────────────────────────────────────────

def test_run_meta_is_found_in_its_own_team(orch):
    register(orch, "api", "backend")
    register(orch, "docs", "writer")
    orch._save_meta({"run_id": "20260809-101010-ab", "orchestrator": "writer",
                     "team": "docs", "status": "running"})
    assert (Path.home() / ".darkarchon" / "docs" / "hermes-runs"
            / "20260809-101010-ab.json").is_file()
    meta = orch._load_meta("20260809-101010-ab")
    assert meta["team"] == "docs"
    assert [r["run_id"] for r in orch.runs()["runs"]] == ["20260809-101010-ab"]


def test_unknown_run_id_is_not_found(orch):
    register(orch, "api", "backend")
    assert orch._load_meta("nope") is None
