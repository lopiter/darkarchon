"""Team-assignment regression: a worker NAME duplicated across teams (e.g. a
stale leftover entry) must not hijack the team. Resolution is by tmux TARGET."""

import pytest

from lib.hub_store import HostStateStore

import dashboard
from dashboard import _all_state_dirs, _lookup_by_target


def test_target_match_beats_stale_same_name_entry():
    # 'homepage-backend' is registered live in the live-team session (window
    # name) AND left over stale in 'default' (a dead session). The live pane
    # reports a window-INDEX target; it must resolve to live-team, never to
    # the stale default.
    teams_by_target = {
        "live-team:homepage-backend": ("live-team", "live-team"),
        # stale leftover, different target
        "default:homepage-backend": ("default", "default"),
    }
    worker = {
        "target": "live-team:2.1",
        "window_name": "homepage-backend",
    }
    assert _lookup_by_target(worker, teams_by_target) == "live-team"


def test_direct_window_index_target_match():
    teams_by_target = {"teamA:1": ("teamA", "teamA")}
    worker = {"target": "teamA:1.0", "window_name": "whatever"}
    assert _lookup_by_target(worker, teams_by_target) == "teamA"


def test_no_match_returns_none():
    teams_by_target = {"teamA:dev": ("teamA", "teamA")}
    worker = {"target": "teamB:2.1", "window_name": "reviewer"}
    assert _lookup_by_target(worker, teams_by_target) is None


def test_empty_target_returns_none():
    assert _lookup_by_target({"target": ""}, {"x:y": ("t", "x")}) is None


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


def test_live_teams_ignore_dead_workers():
    """A team can report several workers and have every one of them dead —
    registry entries outlive their panes. Grading that 'live' both overstates
    it and hides the age the dashboard is supposed to surface."""
    workers = [
        {"host": "h", "team_name": "gone", "state": "dead"},
        {"host": "h", "team_name": "gone", "state": "dead"},
        {"host": "h", "team_name": "working", "state": "dead"},
        {"host": "h", "team_name": "working", "state": "idle"},
    ]

    assert dashboard._live_team_keys(workers) == {("h", "working")}


def test_live_teams_count_every_non_dead_state():
    """Only 'dead' is evidence of absence; anything else is a running pane."""
    workers = [
        {"host": "h", "team_name": f"t{i}", "state": s}
        for i, s in enumerate(["idle", "busy", "awaiting_user", "compacting",
                               "rate_limited", "typed", "unknown"])
    ]

    assert len(dashboard._live_team_keys(workers)) == len(workers)


def test_live_teams_skip_workers_without_a_team():
    assert dashboard._live_team_keys(
        [{"host": "h", "state": "idle"}, {"host": "h", "team_name": "", "state": "idle"}]
    ) == set()


def test_same_team_name_on_two_hosts_stays_separate():
    """Team names are only unique within a host. A live `voc` on one machine
    must not vouch for an abandoned `voc` on another."""
    workers = [
        {"host": "alpha", "team_name": "voc", "state": "idle"},
        {"host": "beta", "team_name": "voc", "state": "dead"},
    ]

    assert dashboard._live_team_keys(workers) == {("alpha", "voc")}


def test_dedicated_session_moves_empty_orchestrator_out_of_fleet():
    """Hermes registers voc-1 in the fleet dir (3hour-team) but gives it a
    dedicated tmux session. Display grouping follows the session, not the
    fleet registry — otherwise empty orchestrators pile up in the fleet."""
    w = {
        "team_name": "3hour-team",
        "session": "voc-1",
        "role": "orchestrator",
        "is_orchestrator": False,
        "spawned_by": "3hour-team",
    }
    dashboard._apply_display_grouping(w)
    assert w["team_name"] == "voc-1"
    assert w["is_orchestrator"] is True
    assert w["spawned_by"] == "3hour-team"  # lineage must not be rewritten


def test_staff_without_session_stay_in_their_assigned_team():
    """3hour's website-ui lives in session 3hour and has no SESSION override.
    Overlay must not yank it into another group."""
    w = {
        "team_name": "3hour",
        "session": "",
        "role": "website-ui",
        "is_orchestrator": False,
        "spawned_by": "3hour",
    }
    dashboard._apply_display_grouping(w)
    assert w["team_name"] == "3hour"
    assert w["is_orchestrator"] is False
    assert w["spawned_by"] == "3hour"


def test_orchestrator_matching_session_keeps_team_name_role_sets_orch():
    """3hour employee: SESSION=3hour already equals the assigned team.
    Team name stays; role=orchestrator still raises the ORCH flag."""
    w = {
        "team_name": "3hour",
        "session": "3hour",
        "role": "orchestrator",
        "is_orchestrator": False,
        "spawned_by": "3hour-team",
    }
    dashboard._apply_display_grouping(w)
    assert w["team_name"] == "3hour"
    assert w["is_orchestrator"] is True


def test_vx_backend_groups_by_session_not_a_plugin_label():
    """employee-groups.json says vx-backend → 'vx'. Using that label would
    split the employee from its sub-team (DARKARCHON_TEAM=vx-backend).
    Display grouping uses the dedicated session, never the plugin label."""
    w = {
        "team_name": "3hour-team",
        "session": "vx-backend",
        "role": "orchestrator",
        "is_orchestrator": False,
        "spawned_by": "3hour-team",
    }
    dashboard._apply_display_grouping(w)
    assert w["team_name"] == "vx-backend"
    assert w["team_name"] != "vx"


def test_missing_session_key_is_a_no_op_on_team():
    w = {"team_name": "3hour-team", "role": "worker", "is_orchestrator": False}
    dashboard._apply_display_grouping(w)
    assert w["team_name"] == "3hour-team"


def test_registry_session_lookup_by_target(hub_at):
    """Hub fallback: SESSION is read from the fleet registry so display
    grouping works even when the agent payload omitted the field."""
    root = hub_at("3hour-team")
    fleet = root / "3hour-team"
    fleet.mkdir()
    (fleet / "workers-runtime.env").write_text(
        "WORKER_voc_1_NAME=voc-1\n"
        "WORKER_voc_1_TARGET=voc-1:voc-1\n"
        "WORKER_voc_1_SESSION=voc-1\n"
        "WORKER_voc_1_WINDOW_ID=@7\n"
        "WORKER_staff_NAME=website-ui\n"
        "WORKER_staff_TARGET=3hour:website-ui\n"
    )
    sessions = dashboard._registered_sessions_by_target()
    # Values carry the session the row was registered in, so a window-id hit
    # can be sanity-checked against the pane that claims it.
    assert sessions["voc-1:voc-1"] == ("voc-1", "voc-1")
    assert sessions["@7"] == ("voc-1", "voc-1")
    assert "3hour:website-ui" not in sessions


def test_forced_orchestrator_flag_kept_when_role_is_not_orchestrator():
    """A pane tagged is_orchestrator via dispatch history stays tagged even
    if its role is not the string 'orchestrator'."""
    w = {
        "team_name": "other",
        "session": "",
        "role": "worker",
        "is_orchestrator": True,
    }
    dashboard._apply_display_grouping(w)
    assert w["is_orchestrator"] is True


def _pane(target, name, **extra):
    w = {
        "target": target,
        "name": name,
        "state": "idle",
        "process": "claude",
        "cwd": "/",
        "kind": extra.pop("kind", "registered"),
        "role": extra.pop("role", "worker"),
        "window_name": extra.pop("window_name", name),
        "window_id": extra.pop("window_id", ""),
        "session": extra.pop("session", ""),
        "external": extra.pop("external", False),
    }
    w.update(extra)
    return w


def _status_workers(hub_at, monkeypatch, *, own="mine", teams=(), markers=None,
                    registries=None, workers=None, host="h", hub_host="h"):
    """Drive Handler._status's collector against a throwaway state root.

    `markers` is {team_name: pane_key} written to orchestrator.txt.
    `registries` is {team_name: workers-runtime.env text} overlaying _make_team.
    `host` is the host id the panes are reported under; `hub_host` is the host
    the hub itself runs on. They match by default — set them apart to model a
    remote machine, whose tmux ids mean nothing against the hub's own disk.
    """
    root = hub_at(own)
    for t in teams:
        _make_team(root, t)
    own_dir = root / own
    own_dir.mkdir(exist_ok=True)
    if not (own_dir / "workers-runtime.env").exists():
        (own_dir / "workers-runtime.env").write_text("WORKER_x_TARGET=x:1\n")
    for team, pane in (markers or {}).items():
        d = root / team
        d.mkdir(exist_ok=True)
        if not (d / "workers-runtime.env").exists():
            (d / "workers-runtime.env").write_text("WORKER_x_TARGET=x:1\n")
        (d / "orchestrator.txt").write_text(pane + "\n")
    for team, text in (registries or {}).items():
        d = root / team
        d.mkdir(exist_ok=True)
        (d / "workers-runtime.env").write_text(text)
    store = HostStateStore(stale_after_seconds=60)
    monkeypatch.setattr(dashboard, "STORE", store)
    monkeypatch.setattr(dashboard, "HUB_HOST_ID", hub_host)
    list(store.update_host(host, workers or []))
    return dashboard._collect_status()["workers"]


def test_status_session_eq_team_explicit_marker_sets_orch_keeps_team(
        hub_at, monkeypatch):
    """dark:1.1 lives in session `dark`, which is a known team, so the
    session heuristic would force is_orchestrator=False. orchestrator.txt
    in the darkarchon team names that pane — badge must still be True,
    team_name must stay `dark`."""
    workers = _status_workers(
        hub_at, monkeypatch,
        own="darkarchon",
        teams=("dark", "darkarchon"),
        markers={"darkarchon": "dark:1.1"},
        workers=[_pane("dark:1.1", "darkarchon", role="worker-invited",
                       external=True, window_name="grok")],
    )
    w = next(x for x in workers if x["target"] == "dark:1.1")
    assert w["team_name"] == "dark"
    assert w["is_orchestrator"] is True


def test_status_session_neq_team_explicit_marker_still_orch(hub_at, monkeypatch):
    """hermes:1.1 is not a known team session; marker in 3hour-team. Today's
    elif forced_team path already sets orch=True — must not regress."""
    workers = _status_workers(
        hub_at, monkeypatch,
        own="3hour-team",
        teams=("3hour-team",),
        markers={"3hour-team": "hermes:1.1"},
        workers=[_pane("hermes:1.1", "hermes", role="worker",
                       window_name="hermes")],
    )
    w = next(x for x in workers if x["target"] == "hermes:1.1")
    assert w["is_orchestrator"] is True


def test_status_unmarked_worker_is_not_orch(hub_at, monkeypatch):
    workers = _status_workers(
        hub_at, monkeypatch,
        own="dark",
        teams=("dark",),
        workers=[_pane("dark:2.1", "website-ui", role="website-ui",
                       window_name="website-ui")],
    )
    w = next(x for x in workers if x["name"] == "website-ui")
    assert w["is_orchestrator"] is False
    assert w["team_name"] == "dark"


def test_status_registered_orchestrator_role_still_orch(hub_at, monkeypatch):
    """Yesterday's overlay: role=orchestrator must still raise the flag
    through the real _collect_status path, not only _apply_display_grouping."""
    workers = _status_workers(
        hub_at, monkeypatch,
        own="3hour-team",
        teams=("3hour-team",),
        registries={
            "3hour-team": (
                "WORKER_voc_1_NAME=voc-1\n"
                "WORKER_voc_1_TARGET=voc-1:voc-1\n"
                "WORKER_voc_1_SESSION=voc-1\n"
                "WORKER_voc_1_ROLE=orchestrator\n"
            ),
        },
        workers=[_pane("voc-1:voc-1.0", "voc-1", role="orchestrator",
                       window_name="voc-1", kind="registered")],
    )
    w = next(x for x in workers if x["name"] == "voc-1")
    assert w["is_orchestrator"] is True


def test_status_stale_index_marker_does_not_badge_staff(hub_at, monkeypatch):
    """orchestrator.txt still says 3hour:1.1 from when the orch sat at
    index 1. After respawn, index 1 is website-ui (registered, role=
    website-ui). Must not inherit the orch badge."""
    workers = _status_workers(
        hub_at, monkeypatch,
        own="3hour",
        teams=("3hour",),
        markers={"3hour": "3hour:1.1"},
        registries={
            "3hour": (
                "WORKER_ui_NAME=website-ui\n"
                "WORKER_ui_TARGET=3hour:website-ui\n"
                "WORKER_ui_ROLE=website-ui\n"
                "WORKER_ui_WINDOW_ID=@11\n"
            ),
        },
        workers=[_pane("3hour:1.1", "website-ui", role="website-ui",
                       window_name="website-ui", window_id="@11",
                       kind="registered")],
    )
    w = next(x for x in workers if x["name"] == "website-ui")
    assert w["team_name"] == "3hour"
    assert w["is_orchestrator"] is False


def test_status_new_marker_does_not_badge_discovered_at_old_index(
        hub_at, monkeypatch):
    """New-format marker (has @id). Index 1 is now an unregistered claude
    pane — staff guard would let it through. Must not match on pane key."""
    workers = _status_workers(
        hub_at, monkeypatch,
        own="3hour",
        teams=("3hour",),
        markers={"3hour": "3hour:1.1 @14"},
        workers=[
            _pane("3hour:1.1", "3hour:1.1", role="", kind="discovered",
                  window_name="2.1.239", window_id="@99"),
            _pane("3hour:3.1", "3hour", role="orchestrator",
                  window_name="3hour", window_id="@14", kind="registered"),
        ],
    )
    by_tgt = {x["target"]: x for x in workers}
    assert by_tgt["3hour:1.1"]["is_orchestrator"] is False
    assert by_tgt["3hour:3.1"]["is_orchestrator"] is True


def test_status_legacy_marker_still_matches_pane_key(hub_at, monkeypatch):
    """No window_id on the marker — pane key remains the fallback."""
    workers = _status_workers(
        hub_at, monkeypatch,
        own="darkarchon",
        teams=("dark", "darkarchon"),
        markers={"darkarchon": "dark:1.1"},
        workers=[_pane("dark:1.1", "darkarchon", role="worker-invited",
                       external=True, window_name="grok")],
    )
    w = next(x for x in workers if x["target"] == "dark:1.1")
    assert w["is_orchestrator"] is True


def test_status_marker_window_id_beats_reused_index(hub_at, monkeypatch):
    """New marker format 'pane @window_id'. Index 1 is now staff, but the
    orch pane kept window id @14 even after it moved to index 3."""
    workers = _status_workers(
        hub_at, monkeypatch,
        own="3hour",
        teams=("3hour",),
        markers={"3hour": "3hour:1.1 @14"},
        registries={
            "3hour": (
                "WORKER_orch_NAME=3hour\n"
                "WORKER_orch_TARGET=3hour:3hour\n"
                "WORKER_orch_ROLE=orchestrator\n"
                "WORKER_orch_WINDOW_ID=@14\n"
                "WORKER_ui_NAME=website-ui\n"
                "WORKER_ui_TARGET=3hour:website-ui\n"
                "WORKER_ui_ROLE=website-ui\n"
                "WORKER_ui_WINDOW_ID=@11\n"
            ),
        },
        workers=[
            _pane("3hour:1.1", "website-ui", role="website-ui",
                  window_name="website-ui", window_id="@11", kind="registered"),
            _pane("3hour:3.1", "3hour", role="orchestrator",
                  window_name="3hour", window_id="@14", kind="registered"),
        ],
    )
    by_name = {x["name"]: x for x in workers}
    assert by_name["website-ui"]["is_orchestrator"] is False
    assert by_name["3hour"]["is_orchestrator"] is True


def test_status_corrupt_orchestrator_txt_does_not_500(hub_at, monkeypatch):
    root = hub_at("darkarchon")
    _make_team(root, "darkarchon")
    (root / "darkarchon" / "orchestrator.txt").write_bytes(b"dark:1.1\xed\xa0\x80\n")
    store = HostStateStore(stale_after_seconds=60)
    monkeypatch.setattr(dashboard, "STORE", store)
    list(store.update_host("h", [_pane("dark:1.1", "darkarchon", role="worker-invited",
                                       external=True, window_name="grok")]))
    data = dashboard._collect_status()
    assert "workers" in data


def test_parse_orch_marker_line_legacy_and_window_id():
    assert dashboard._parse_orch_marker("dark:1.1") == ("dark:1.1", "")
    assert dashboard._parse_orch_marker("3hour:1.1 @14") == ("3hour:1.1", "@14")
    assert dashboard._parse_orch_marker("  hermes:1.1  @9 \n") == ("hermes:1.1", "@9")
    assert dashboard._parse_orch_marker("") == ("", "")


def test_status_fills_session_from_registry_before_grouping(hub_at, monkeypatch):
    """Wiring: _collect_status must copy WORKER_*_SESSION onto the worker
    before _apply_display_grouping. Swapping those two steps would leave
    voc-1 in the fleet group even though the registry records SESSION."""
    workers = _status_workers(
        hub_at, monkeypatch,
        own="3hour-team",
        teams=("3hour-team",),
        registries={
            "3hour-team": (
                "WORKER_voc_1_NAME=voc-1\n"
                "WORKER_voc_1_TARGET=voc-1:voc-1\n"
                "WORKER_voc_1_SESSION=voc-1\n"
                "WORKER_voc_1_ROLE=orchestrator\n"
            ),
        },
        workers=[_pane("voc-1:voc-1.0", "voc-1", role="orchestrator",
                       window_name="voc-1", kind="registered", session="")],
    )
    w = next(x for x in workers if x["name"] == "voc-1")
    assert w["session"] == "voc-1"
    assert w["team_name"] == "voc-1"


# ─── Cross-host tmux identifier collisions ──────────────────────────────────
# tmux numbers window ids per server, i.e. per machine. The hub reads every
# registry, marker and state dir off its OWN disk, so those keys describe the
# hub's machine only. Matching them against a pane reported by another host
# compares two unrelated namespaces. Observed in production: MacBook-Pro-2's
# `moto` orchestrator and second-mac's plain shell were both `@17`, and the
# remote shell was display-grouped under `moto`.

def test_remote_pane_not_grouped_by_colliding_window_id(hub_at, monkeypatch):
    """The production bug: a DISCOVERED pane on another host shares a window
    id with a hub-local worker registered to a dedicated session."""
    workers = _status_workers(
        hub_at, monkeypatch, own="mine", teams=["moto"],
        registries={"moto": (
            "WORKER_moto_NAME=moto\n"
            "WORKER_moto_TARGET=moto:moto\n"
            "WORKER_moto_SESSION=moto\n"
            "WORKER_moto_WINDOW_ID=@17\n"
        )},
        host="second-mac", hub_host="MacBook-Pro-2",
        workers=[_pane("6:1.1", "6:1.1", kind="discovered", role="",
                       window_name="2.1.247", window_id="@17")],
    )
    w = workers[0]
    assert w["team_name"] == "6", "remote pane must keep its own tmux session"
    assert not w.get("session")


def test_local_pane_still_grouped_by_window_id(hub_at, monkeypatch):
    """The guard must not cost the hub-local case it exists to serve."""
    workers = _status_workers(
        hub_at, monkeypatch, own="mine", teams=["fleet"],
        registries={"fleet": (
            "WORKER_voc_NAME=voc-1\n"
            "WORKER_voc_TARGET=fleet:voc-1\n"
            "WORKER_voc_SESSION=voc-1\n"
            "WORKER_voc_WINDOW_ID=@17\n"
        )},
        host="MacBook-Pro-2", hub_host="MacBook-Pro-2",
        workers=[_pane("fleet:2.1", "voc-1", window_name="voc-1",
                       window_id="@17")],
    )
    assert workers[0]["team_name"] == "voc-1"


def test_worker_without_host_is_treated_as_local(hub_at, monkeypatch):
    """Single-machine setups (and payloads predating multi-host reporting)
    report no host at all — they must keep the local shortcuts."""
    workers = _status_workers(
        hub_at, monkeypatch, own="mine", teams=["fleet"],
        registries={"fleet": (
            "WORKER_voc_NAME=voc-1\n"
            "WORKER_voc_TARGET=fleet:voc-1\n"
            "WORKER_voc_SESSION=voc-1\n"
            "WORKER_voc_WINDOW_ID=@17\n"
        )},
        host="", hub_host="whatever",
        workers=[_pane("fleet:2.1", "voc-1", window_name="voc-1",
                       window_id="@17")],
    )
    assert workers[0]["team_name"] == "voc-1"


def test_remote_marker_window_id_does_not_badge_orchestrator(hub_at, monkeypatch):
    """orchestrator.txt is written on the hub's disk about the hub's tmux —
    its window id must not badge a same-id pane on another machine."""
    workers = _status_workers(
        hub_at, monkeypatch, own="mine", markers={"mine": "mine:1.1 @17"},
        host="second-mac", hub_host="MacBook-Pro-2",
        workers=[_pane("6:1.1", "6:1.1", kind="discovered", role="",
                       window_id="@17")],
    )
    assert workers[0]["is_orchestrator"] is False


def test_discovered_pane_never_pulled_out_by_a_registry_session(hub_at, monkeypatch):
    """Second guard, independent of host: the dedicated-session map is built
    from registry rows, so a pane that matched no registration must not be
    regrouped by one. Mirrors the kind check the team_name branch already had."""
    workers = _status_workers(
        hub_at, monkeypatch, own="mine", teams=["fleet"],
        registries={"fleet": (
            "WORKER_voc_NAME=voc-1\n"
            "WORKER_voc_TARGET=solo:1\n"
            "WORKER_voc_SESSION=voc-1\n"
        )},
        host="h", hub_host="h",
        workers=[_pane("solo:1.1", "solo:1.1", kind="discovered", role="",
                       window_name="zsh")],
    )
    assert workers[0]["team_name"] == "solo"


def test_stale_window_id_in_another_session_is_ignored(hub_at, monkeypatch):
    """Intra-host variant: tmux restarts ids at @0 when its server does, so a
    stale row can collide with an unrelated new window on the same machine.
    Same guard worker_resolver._window_id_match already applies."""
    workers = _status_workers(
        hub_at, monkeypatch, own="mine", teams=["fleet"],
        registries={"fleet": (
            "WORKER_old_NAME=old\n"
            "WORKER_old_TARGET=deadsess:old\n"
            "WORKER_old_SESSION=archived\n"
            "WORKER_old_WINDOW_ID=@3\n"
        )},
        host="h", hub_host="h",
        workers=[_pane("newsess:1.1", "newsess:1.1", kind="discovered",
                       role="", window_name="zsh", window_id="@3")],
    )
    assert workers[0]["team_name"] == "newsess"
