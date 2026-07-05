"""Tests for the unified worker-state resolver (lib/worker_state.py).

The merge policy (synthesize) is pure and gets the bulk of the coverage; resolve()
is exercised with injected tmux/capture fns so no live tmux is needed.
"""

import json
import time

from lib import worker_state as ws


# ── synthesize: the pure merge policy ──────────────────────────────────────
def test_dead_beats_everything():
    r = ws.synthesize(hook={"state": "busy"}, scrape={"state": "busy"}, is_dead=True)
    assert r["state"] == "dead"
    assert r["source"] == "liveness"


def test_scrape_meta_states_always_win():
    # rate_limited is a scrape-only signal hooks can't emit — must not be masked
    # by a stale hook that still says busy.
    r = ws.synthesize(
        hook={"state": "busy", "detail": ""},
        scrape={"state": "rate_limited", "detail": "5h: 100%"},
        is_dead=False,
    )
    assert r["state"] == "rate_limited"
    assert r["source"] == "scrape-meta"


def test_no_hook_uses_scrape_verbatim():
    r = ws.synthesize(hook=None, scrape={"state": "busy", "detail": "Whisking…"}, is_dead=False)
    assert r["state"] == "busy"
    assert r["source"] == "scrape"


def test_stuck_busy_hook_self_heals_when_scrape_idle():
    # hook missed a Stop event and is pinned busy; scrape sees an empty prompt.
    r = ws.synthesize(
        hook={"state": "busy", "detail": ""},
        scrape={"state": "idle", "detail": ""},
        is_dead=False,
    )
    assert r["state"] == "idle"
    assert r["source"] == "scrape(hook-stale)"


def test_hook_idle_with_typed_prompt_becomes_unsent():
    # user typed but hasn't hit Enter — hooks can't see the prompt line.
    r = ws.synthesize(
        hook={"state": "idle", "detail": ""},
        scrape={"state": "unsent", "detail": "git push"},
        is_dead=False,
    )
    assert r["state"] == "unsent"
    assert r["source"] == "scrape-overlay"


def test_hook_awaiting_user_is_authoritative():
    # hook carries the rich permission detail scrape can't; hook wins over idle scrape.
    r = ws.synthesize(
        hook={"state": "awaiting_user", "detail": "Permission required: Bash"},
        scrape={"state": "idle", "detail": ""},
        is_dead=False,
    )
    assert r["state"] == "awaiting_user"
    assert r["detail"] == "Permission required: Bash"
    assert r["source"] == "hook"


def test_hook_busy_and_scrape_busy_stays_busy():
    r = ws.synthesize(
        hook={"state": "busy", "detail": "turn"},
        scrape={"state": "busy", "detail": "Whisking…"},
        is_dead=False,
    )
    assert r["state"] == "busy"
    assert r["source"] == "hook"


# ── read_hook_state ────────────────────────────────────────────────────────
def test_read_hook_state_roundtrip(tmp_path):
    d = tmp_path / "states"
    d.mkdir()
    (d / "backend.json").write_text(
        json.dumps({"state": "awaiting_user", "detail": "perm", "ts_epoch": 1, "event": "Notification"})
    )
    got = ws.read_hook_state(tmp_path, "backend")
    assert got["state"] == "awaiting_user"
    assert got["detail"] == "perm"


def test_read_hook_state_sanitizes_name(tmp_path):
    # worker name "hd-si" → file "hd_si.json" (matches _lib.sh safe_name).
    d = tmp_path / "states"
    d.mkdir()
    (d / "hd_si.json").write_text(json.dumps({"state": "busy"}))
    assert ws.read_hook_state(tmp_path, "hd-si")["state"] == "busy"


def test_read_hook_state_missing_returns_none(tmp_path):
    assert ws.read_hook_state(tmp_path, "nobody") is None


def test_read_hook_state_corrupt_returns_none(tmp_path):
    d = tmp_path / "states"
    d.mkdir()
    (d / "x.json").write_text("{not json")
    assert ws.read_hook_state(tmp_path, "x") is None


# ── liveness ───────────────────────────────────────────────────────────────
def test_liveness_dead_when_session_down(tmp_path):
    is_dead, reason = ws.liveness_is_dead(tmp_path, "w", session_running=False)
    assert is_dead
    assert "session" in reason


def test_liveness_dead_when_heartbeat_stale(tmp_path):
    hb = tmp_path / "heartbeats"
    hb.mkdir()
    old = time.time() - 999
    (hb / "w.json").write_text(json.dumps({"worker": "w", "pid": 999999, "last_seen_epoch": old}))
    is_dead, reason = ws.liveness_is_dead(tmp_path, "w", session_running=True)
    assert is_dead
    assert "stale" in reason


def test_liveness_alive_with_fresh_heartbeat_live_pid(tmp_path):
    import os

    hb = tmp_path / "heartbeats"
    hb.mkdir()
    (hb / "w.json").write_text(
        json.dumps({"worker": "w", "pid": os.getpid(), "last_seen_epoch": time.time()})
    )
    is_dead, _ = ws.liveness_is_dead(tmp_path, "w", session_running=True)
    assert not is_dead


def test_liveness_alive_with_no_heartbeat(tmp_path):
    # invited/legacy worker: no heartbeat file, session up → not dead.
    is_dead, _ = ws.liveness_is_dead(tmp_path, "w", session_running=True)
    assert not is_dead


# ── lookup_worker ──────────────────────────────────────────────────────────
def test_lookup_worker_by_name(tmp_path):
    (tmp_path / "workers-runtime.env").write_text(
        "WORKER_backend_NAME=backend\n"
        "WORKER_backend_TARGET=myteam:backend\n"
        "WORKER_backend_DIR=/repo/backend\n"
        "WORKER_backend_KIND=claude\n"
    )
    info = ws.lookup_worker(tmp_path, "backend")
    assert info == {"target": "myteam:backend", "kind": "claude", "cwd": "/repo/backend"}


def test_lookup_worker_unknown_returns_none(tmp_path):
    (tmp_path / "workers-runtime.env").write_text("")
    assert ws.lookup_worker(tmp_path, "ghost") is None


# ── resolve (injected tmux/capture) ────────────────────────────────────────
def _write_registry(tmp_path, name="backend", kind="claude"):
    (tmp_path / "workers-runtime.env").write_text(
        f"WORKER_{name}_NAME={name}\nWORKER_{name}_TARGET=myteam:{name}\nWORKER_{name}_KIND={kind}\n"
    )


def test_resolve_unknown_worker(tmp_path):
    _write_registry(tmp_path)
    r = ws.resolve("ghost", tmp_path, session_running_fn=lambda s: True, capture_fn=lambda t, with_ansi=False: "")
    assert r["state"] == "unknown"
    assert r["target"] is None


def test_resolve_dead_when_session_down(tmp_path):
    _write_registry(tmp_path)
    r = ws.resolve("backend", tmp_path, session_running_fn=lambda s: False, capture_fn=lambda t, with_ansi=False: "")
    assert r["state"] == "dead"


def test_resolve_scrape_busy_no_hook(tmp_path):
    _write_registry(tmp_path)

    def cap(target, with_ansi=False):
        # a claude busy frame: gerund + ellipsis above the prompt separator
        return "✻ Whisking…\n────────────\n❯ "

    r = ws.resolve("backend", tmp_path, session_running_fn=lambda s: True, capture_fn=cap)
    assert r["state"] == "busy"
    assert r["source"] == "scrape"


def test_resolve_hook_awaiting_overrides_idle_scrape(tmp_path):
    _write_registry(tmp_path)
    states = tmp_path / "states"
    states.mkdir()
    (states / "backend.json").write_text(
        json.dumps({"state": "awaiting_user", "detail": "Permission required: Bash git push"})
    )

    def cap(target, with_ansi=False):
        return "────────────\n❯ "  # idle-looking prompt

    r = ws.resolve("backend", tmp_path, session_running_fn=lambda s: True, capture_fn=cap)
    assert r["state"] == "awaiting_user"
    assert "Permission" in r["detail"]
    assert r["source"] == "hook"


# ── observer overlay ───────────────────────────────────────────────────────
def test_annotate_overlays_hook_awaiting(tmp_path):
    states = tmp_path / "states"
    states.mkdir()
    (states / "backend.json").write_text(json.dumps({"state": "awaiting_user", "detail": "perm msg"}))
    workers = [{"name": "backend", "state": "idle", "detail": ""}]
    out = ws.annotate_workers_with_hooks(workers, tmp_path)
    assert out[0]["state"] == "awaiting_user"
    assert out[0]["detail"] == "perm msg"
    assert out[0]["state_source"] == "hook"


def test_annotate_leaves_dead_alone(tmp_path):
    states = tmp_path / "states"
    states.mkdir()
    (states / "backend.json").write_text(json.dumps({"state": "busy"}))
    workers = [{"name": "backend", "state": "dead", "detail": "pid gone"}]
    out = ws.annotate_workers_with_hooks(workers, tmp_path)
    assert out[0]["state"] == "dead"


def test_annotate_untouched_without_hook_file(tmp_path):
    workers = [{"name": "codexw", "state": "busy", "detail": "Working (3s"}]
    out = ws.annotate_workers_with_hooks(workers, tmp_path)
    assert out[0]["state"] == "busy"
    assert "state_source" not in out[0]
