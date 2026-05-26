"""Tests for agent.report_once() — scans tmux, merges registry, posts to hub."""

from unittest.mock import patch

from agent import AgentConfig, report_once


def test_report_once_posts_resolved_workers_to_hub(tmp_path):
    registry_path = tmp_path / "workers-runtime.env"
    registry_path.write_text(
        "WORKER_app1_NAME=app1\n"
        "WORKER_app1_TARGET=hostA:1\n"
        "WORKER_app1_ROLE=service-role\n"
        "WORKER_app1_DIR=/x\n"
        "WORKER_app1_EXTERNAL=1\n"
    )

    cfg = AgentConfig(
        hub_url="http://hub.test",
        host_id="main-pc",
        registry_path=registry_path,
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
        registry_path=tmp_path / "nonexistent.env",
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
