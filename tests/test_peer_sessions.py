"""Unit tests for lib/peer_sessions.py — the ~/.claude/sessions join."""

import json
import os

from lib.peer_sessions import annotate_workers_with_peer_names, read_peer_sessions


def _write(directory, filename, record):
    (directory / filename).write_text(json.dumps(record))


def _session_record(**over):
    rec = {
        "pid": os.getpid(),  # a pid guaranteed alive during the test
        "name": "darkarchon-c3",
        "nameSource": "derived",
        "tmux": "dark:@0.%0",
        "sessionId": "ad0f0f7c-e586-4771-bf13-6ee484336e29",
    }
    rec.update(over)
    return rec


def test_read_peer_sessions_maps_tmux_pane_to_name(tmp_path):
    _write(tmp_path, "6280.json", _session_record())
    reg = read_peer_sessions(tmp_path)
    assert reg == {
        "dark:@0.%0": {
            "peer_name": "darkarchon-c3",
            "session_id": "ad0f0f7c-e586-4771-bf13-6ee484336e29",
        }
    }


def test_read_peer_sessions_skips_dead_pids(tmp_path):
    # A registry file whose session crashed lingers with a dead pid; a recycled
    # pid slot must not resurrect it. 2**22 exceeds macOS/Linux default pid_max.
    _write(tmp_path, "1.json", _session_record(pid=2**22))
    assert read_peer_sessions(tmp_path) == {}


def test_read_peer_sessions_skips_malformed_records(tmp_path):
    (tmp_path / "bad.json").write_text("{not json")
    _write(tmp_path, "list.json", ["not", "a", "dict"])
    _write(tmp_path, "noname.json", _session_record(name=""))
    _write(tmp_path, "notmux.json", _session_record(tmux=None))
    _write(tmp_path, "nopid.json", _session_record(pid="6280"))
    assert read_peer_sessions(tmp_path) == {}


def test_read_peer_sessions_missing_dir(tmp_path):
    assert read_peer_sessions(tmp_path / "absent") == {}


def test_annotate_stamps_matching_worker_only():
    workers = [
        # claude pane hosting the registered session
        {"target": "dark:1.0", "window_id": "@0", "pane_id": "%0", "process": "claude"},
        # codex pane — never in the sessions registry
        {"target": "dark:2.0", "window_id": "@1", "pane_id": "%1", "process": "codex"},
        # pre-pane_id agent payload (field absent) — must pass through untouched
        {"target": "dark:3.0", "window_id": "@2", "process": "claude"},
    ]
    reg = {"dark:@0.%0": {"peer_name": "darkarchon-c3", "session_id": "x"}}
    out = annotate_workers_with_peer_names(workers, reg)
    assert out[0]["peer_name"] == "darkarchon-c3"
    assert "peer_name" not in out[1]
    assert "peer_name" not in out[2]


def test_annotate_joins_by_immutable_ids_not_indices():
    # The registry key uses window/pane IDs; a worker whose window index moved
    # (windows renumber as siblings close) still matches by @id.%id.
    workers = [{"target": "dark:7.3", "window_id": "@0", "pane_id": "%0"}]
    reg = {"dark:@0.%0": {"peer_name": "darkarchon-c3"}}
    assert annotate_workers_with_peer_names(workers, reg)[0]["peer_name"] == "darkarchon-c3"


def test_annotate_empty_registry_is_identity():
    workers = [{"target": "dark:1.0", "window_id": "@0", "pane_id": "%0"}]
    assert annotate_workers_with_peer_names(workers, {}) is workers
