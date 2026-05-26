"""Tests for the worker resolver — merges tmux scan results with registry metadata."""

from lib.worker_resolver import parse_registry_text, resolve_workers


def test_parse_registry_extracts_name_target_role():
    text = """
# spawned 2026-05-21T00:00:00Z
WORKER_svc1_NAME=svc1
WORKER_svc1_TARGET=teamA:svc1
WORKER_svc1_DIR=/Users/u/work/svc1
WORKER_svc1_ROLE=some-role

# invited 2026-05-21T00:00:01Z
WORKER_app1_NAME=app1
WORKER_app1_TARGET=hostA:1
WORKER_app1_DIR=/Users/u/work/app1
WORKER_app1_ROLE=service-role
WORKER_app1_EXTERNAL=1
"""
    registry = parse_registry_text(text)
    assert registry["teamA:svc1"]["name"] == "svc1"
    assert registry["teamA:svc1"]["role"] == "some-role"
    assert registry["teamA:svc1"]["external"] is False
    assert registry["hostA:1"]["external"] is True


def test_resolve_workers_uses_registry_metadata_when_target_matches():
    scanned = [
        {
            "target": "teamA:svc1.0",
            "process": "claude",
            "cwd": "/x",
            "pane_pid": "1",
            "state": "busy",
            "detail": "Whisking…",
        }
    ]
    registry = {
        "teamA:svc1": {
            "name": "svc1",
            "role": "some-role",
            "cwd": "/Users/u/work/svc1",
            "external": False,
        }
    }
    workers = resolve_workers(scanned, registry)
    assert workers[0]["name"] == "svc1"
    assert workers[0]["role"] == "some-role"
    assert workers[0]["kind"] == "registered"


def test_resolve_workers_falls_back_to_auto_id_when_no_registry_match():
    scanned = [
        {
            "target": "stray:0.0",
            "process": "claude",
            "cwd": "/Users/u/random",
            "pane_pid": "9",
            "state": "idle",
            "detail": "",
        }
    ]
    workers = resolve_workers(scanned, registry={})
    assert workers[0]["name"] == "stray:0.0"
    assert workers[0]["role"] == ""
    assert workers[0]["kind"] == "discovered"


def test_resolve_workers_handles_window_only_target_in_registry():
    """Registry stores 'session:window' but scanner reports 'session:window.pane' — should still match."""
    scanned = [
        {
            "target": "hostA:1.0",
            "process": "claude",
            "cwd": "/x",
            "pane_pid": "1",
            "state": "idle",
            "detail": "",
        }
    ]
    registry = {"hostA:1": {"name": "app1", "role": "service-role", "cwd": "/x", "external": True}}
    workers = resolve_workers(scanned, registry)
    assert workers[0]["name"] == "app1"
    assert workers[0]["kind"] == "registered"


def test_resolve_workers_matches_by_window_name_when_registry_uses_window_name():
    """Registry stores 'session:window-name' (spawn-worker convention) but scanner
    reports 'session:window-index.pane'. Resolver should match via window_name field."""
    scanned = [
        {
            "target": "myteam:2.1",
            "process": "claude",
            "cwd": "/x",
            "pane_pid": "1",
            "window_name": "backend",
            "state": "idle",
            "detail": "",
        }
    ]
    registry = {
        "myteam:backend": {
            "name": "backend",
            "role": "backend-role",
            "cwd": "/Users/u/work/backend",
            "external": False,
        }
    }
    workers = resolve_workers(scanned, registry)
    assert workers[0]["name"] == "backend"
    assert workers[0]["role"] == "backend-role"
    assert workers[0]["kind"] == "registered"
