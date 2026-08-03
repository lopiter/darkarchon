"""Tests for agent.report_once() — scans tmux, merges registry, posts to hub."""

from unittest.mock import patch

from agent import AgentConfig, _discover_state_dirs, _merge_registries, report_once


def _write_registry(state_dir, worker, target, role="service-role"):
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "workers-runtime.env").write_text(
        f"WORKER_{worker}_NAME={worker}\n"
        f"WORKER_{worker}_TARGET={target}\n"
        f"WORKER_{worker}_ROLE={role}\n"
        f"WORKER_{worker}_DIR=/x\n"
        f"WORKER_{worker}_EXTERNAL=1\n"
    )
    return state_dir / "workers-runtime.env"


def test_report_once_posts_resolved_workers_to_hub(tmp_path):
    _write_registry(tmp_path / "teamA", "app1", "hostA:1")

    cfg = AgentConfig(
        hub_url="http://hub.test",
        host_id="main-pc",
        state_root=tmp_path,
        llm_processes=("claude",),
    )

    fake_scan = [
        {
            "target": "hostA:1.0",
            "process": "claude",
            "window_name": "app1-window",
            "cwd": "/x",
            "pane_pid": "1",
            "state": "idle",
            "detail": "",
        }
    ]
    captured = {}

    def fake_post(url: str, payload: dict, timeout: float = 3.0):
        captured["url"] = url
        captured["payload"] = payload
        return 200

    with patch("agent.scan_panes", return_value=fake_scan):
        with patch("agent._http_post_json", side_effect=fake_post):
            report_once(cfg)

    assert captured["url"] == "http://hub.test/api/hosts/main-pc/state"
    workers = captured["payload"]["workers"]
    assert len(workers) == 1
    assert workers[0]["name"] == "app1"
    assert workers[0]["kind"] == "registered"


def test_report_once_works_without_registry(tmp_path):
    cfg = AgentConfig(
        hub_url="http://hub.test",
        host_id="remote-pc",
        state_root=tmp_path / "nonexistent",
        llm_processes=("claude",),
    )
    fake_scan = [
        {
            "target": "5:0.0",
            "process": "claude",
            "window_name": "claude",
            "cwd": "/",
            "pane_pid": "1",
            "state": "idle",
            "detail": "",
        }
    ]
    captured = {}

    def fake_post(url: str, payload: dict, timeout: float = 3.0):
        captured["payload"] = payload
        return 200

    with patch("agent.scan_panes", return_value=fake_scan):
        with patch("agent._http_post_json", side_effect=fake_post):
            report_once(cfg)

    assert captured["payload"]["workers"][0]["kind"] == "discovered"


def test_discovery_finds_flat_and_nested_team_dirs(tmp_path):
    """Sibling teams and nested worktree teams both count.

    The two layouts used to be discovered by separate functions at different
    depths, so registry lookup saw one set of teams and heartbeat lookup saw
    another.
    """
    _write_registry(tmp_path / "teamA", "app1", "hostA:1")
    _write_registry(tmp_path / "teamB", "app2", "hostB:1")
    _write_registry(tmp_path / "teamB" / "worktree", "app3", "hostC:1")
    (tmp_path / "no-registry").mkdir()
    (tmp_path / "agent.config").write_text("HUB_URL=http://hub.test\n")

    dirs = _discover_state_dirs(tmp_path)

    assert set(dirs) == {
        tmp_path / "teamA",
        tmp_path / "teamB",
        tmp_path / "teamB" / "worktree",
    }
    assert set(_merge_registries(dirs)) == {"hostA:1", "hostB:1", "hostC:1"}


def test_freshest_registration_wins_on_duplicate_target(tmp_path):
    """A stale entry left in one team must not shadow the live one."""
    import os

    stale = _write_registry(tmp_path / "old-team", "ghost", "hostA:1")
    live = _write_registry(tmp_path / "new-team", "current", "hostA:1")
    os.utime(stale, (1_000_000, 1_000_000))
    os.utime(live, (2_000_000, 2_000_000))

    merged = _merge_registries(_discover_state_dirs(tmp_path))

    assert merged["hostA:1"]["name"] == "current"


def test_report_once_resolves_worker_from_another_team(tmp_path):
    """The agent is host-scoped: a worker registered by any team on the box
    resolves to its name, not to a raw session:window.pane target."""
    _write_registry(tmp_path / "teamA", "app1", "hostA:1")
    _write_registry(tmp_path / "teamB", "app2", "hostB:2")

    cfg = AgentConfig(
        hub_url="http://hub.test",
        host_id="main-pc",
        state_root=tmp_path,
        llm_processes=("claude",),
    )
    fake_scan = [
        {
            "target": "hostB:2.0",
            "process": "claude",
            "window_name": "app2-window",
            "cwd": "/x",
            "pane_pid": "1",
            "state": "idle",
            "detail": "",
        }
    ]
    captured = {}

    with patch("agent.scan_panes", return_value=fake_scan):
        with patch(
            "agent._http_post_json",
            side_effect=lambda url, payload, timeout=3.0: captured.update(payload) or 200,
        ):
            report_once(cfg)

    assert captured["workers"][0]["name"] == "app2"
    assert captured["workers"][0]["kind"] == "registered"
