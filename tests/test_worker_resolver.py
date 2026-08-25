"""Tests for the worker resolver — merges tmux scan results with registry metadata."""

from lib.worker_resolver import parse_registry_file, parse_registry_text, resolve_workers


def test_parse_registry_extracts_agent_kind_defaulting_to_claude():
    text = (
        "WORKER_c1_NAME=c1\nWORKER_c1_TARGET=t:c1\nWORKER_c1_KIND=codex\n"
        "WORKER_legacy_NAME=legacy\nWORKER_legacy_TARGET=t:legacy\n"  # no KIND
    )
    reg = parse_registry_text(text)
    assert reg["t:c1"]["agent_kind"] == "codex"
    assert reg["t:legacy"]["agent_kind"] == "claude"


def test_parse_registry_extracts_spawned_by_defaulting_to_empty():
    text = (
        "WORKER_w1_NAME=w1\nWORKER_w1_TARGET=t:w1\nWORKER_w1_SPAWNED_BY=hermes\n"
        "WORKER_w2_NAME=w2\nWORKER_w2_TARGET=t:w2\n"  # no SPAWNED_BY
    )
    reg = parse_registry_text(text)
    assert reg["t:w1"]["spawned_by"] == "hermes"
    assert reg["t:w2"]["spawned_by"] == ""


def test_parse_registry_extracts_session_defaulting_to_empty():
    """Dedicated host session (spawn --session) is a display-grouping signal.
    A worker registered in the fleet dir but living in its own tmux session
    must carry that session so the hub can group it there, not in the fleet."""
    text = (
        "WORKER_voc_1_NAME=voc-1\n"
        "WORKER_voc_1_TARGET=voc-1:voc-1\n"
        "WORKER_voc_1_SESSION=voc-1\n"
        "WORKER_staff_NAME=website-ui\n"
        "WORKER_staff_TARGET=3hour:website-ui\n"  # no SESSION
    )
    reg = parse_registry_text(text)
    assert reg["voc-1:voc-1"]["session"] == "voc-1"
    assert reg["3hour:website-ui"]["session"] == ""


def test_resolve_workers_carries_spawned_by_through():
    scanned = [
        {"target": "t:w1.0", "process": "claude", "cwd": "/x", "pane_pid": "1", "state": "idle"},
        {"target": "t:zzz.0", "process": "claude", "cwd": "/y", "pane_pid": "2", "state": "idle"},
    ]
    registry = parse_registry_text(
        "WORKER_w1_NAME=w1\nWORKER_w1_TARGET=t:w1\nWORKER_w1_SPAWNED_BY=hermes\n"
    )
    resolved = resolve_workers(scanned, registry)
    assert resolved[0]["spawned_by"] == "hermes"
    assert resolved[1]["spawned_by"] == ""  # discovered pane — no lineage


def test_resolve_workers_carries_session_through():
    scanned = [
        {"target": "voc-1:voc-1.0", "process": "claude", "cwd": "/x", "pane_pid": "1", "state": "idle"},
        {"target": "t:zzz.0", "process": "claude", "cwd": "/y", "pane_pid": "2", "state": "idle"},
    ]
    registry = parse_registry_text(
        "WORKER_voc_1_NAME=voc-1\n"
        "WORKER_voc_1_TARGET=voc-1:voc-1\n"
        "WORKER_voc_1_SESSION=voc-1\n"
        "WORKER_voc_1_ROLE=orchestrator\n"
    )
    resolved = resolve_workers(scanned, registry)
    assert resolved[0]["session"] == "voc-1"
    assert resolved[1]["session"] == ""  # discovered pane


def test_parse_registry_file_tolerates_non_utf8_bytes(tmp_path):
    """A registry may carry a mojibake byte in a free-form ROLE value. Parsing
    must not crash (it would take down the whole multi-team agent merge); the
    ASCII-keyed TARGET/NAME/KIND must still be extracted."""
    p = tmp_path / "workers-runtime.env"
    # Valid ASCII keys + a ROLE value with an invalid UTF-8 continuation byte.
    p.write_bytes(
        b"WORKER_w_NAME=w\nWORKER_w_TARGET=teamX:w\nWORKER_w_KIND=codex\n"
        b"WORKER_w_ROLE=API \xe2\x80 broken\n"
    )
    reg = parse_registry_file(p)
    assert reg["teamX:w"]["name"] == "w"
    assert reg["teamX:w"]["agent_kind"] == "codex"


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


def _pane(**overrides):
    return {
        "target": "myteam:2.1",
        "process": "claude",
        "cwd": "/x",
        "pane_pid": "1",
        "window_name": "backend",
        "window_id": "@7",
        "state": "idle",
        "detail": "",
        **overrides,
    }


def test_window_id_resolves_a_renamed_window():
    """The failure this exists to fix: tmux's automatic-rename replaces the
    window name with claude's version string, so `session:window-name` stops
    matching and a live worker silently reports as 'discovered'."""
    registry = parse_registry_text(
        "WORKER_backend_NAME=backend\n"
        "WORKER_backend_TARGET=myteam:backend\n"
        "WORKER_backend_ROLE=backend-role\n"
        "WORKER_backend_WINDOW_ID=@7\n"
    )

    workers = resolve_workers([_pane(window_name="2.1.220")], registry)

    assert workers[0]["name"] == "backend"
    assert workers[0]["kind"] == "registered"


def test_window_id_from_another_session_is_not_trusted():
    """tmux reassigns ids from @0 after a server restart, so an id alone could
    collide with an unrelated window. The registered session must agree."""
    registry = parse_registry_text(
        "WORKER_backend_NAME=backend\n"
        "WORKER_backend_TARGET=otherteam:backend\n"
        "WORKER_backend_WINDOW_ID=@7\n"
    )

    workers = resolve_workers([_pane(window_name="2.1.220")], registry)

    assert workers[0]["kind"] == "discovered"


def test_name_matching_still_works_without_a_window_id():
    """Entries registered before WINDOW_ID existed must keep resolving."""
    registry = parse_registry_text(
        "WORKER_backend_NAME=backend\n"
        "WORKER_backend_TARGET=myteam:backend\n"
    )

    workers = resolve_workers([_pane(window_id="")], registry)

    assert workers[0]["name"] == "backend"


def test_registry_is_keyed_by_both_target_and_window_id():
    registry = parse_registry_text(
        "WORKER_backend_NAME=backend\n"
        "WORKER_backend_TARGET=myteam:backend\n"
        "WORKER_backend_WINDOW_ID=@7\n"
    )

    assert registry["@7"] is registry["myteam:backend"]
    assert registry["@7"]["window_id"] == "@7"


def test_registry_entries_carry_their_team_state_dir(tmp_path):
    """Each entry records which team registered it, so a later heartbeat or
    hook lookup knows which directory describes THIS pane."""
    team = tmp_path / "small-star"
    team.mkdir()
    (team / "workers-runtime.env").write_text(
        "WORKER_hb_NAME=homepage-backend\nWORKER_hb_TARGET=small-star:homepage-backend\n"
    )

    entries = parse_registry_file(team / "workers-runtime.env")

    assert entries["small-star:homepage-backend"]["state_dir"] == str(team)


def test_resolved_worker_carries_its_state_dir():
    registry = parse_registry_text(
        "WORKER_hb_NAME=homepage-backend\nWORKER_hb_TARGET=t:1\n"
    )
    registry["t:1"]["state_dir"] = "/s/small-star"
    scanned = [{"target": "t:1.1", "process": "claude", "cwd": "/", "pane_pid": "1",
                "window_name": "w", "state": "idle", "detail": ""}]

    assert resolve_workers(scanned, registry)[0]["state_dir"] == "/s/small-star"
