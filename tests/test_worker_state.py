"""Tests for the unified worker-state resolver (lib/worker_state.py).

The merge policy (synthesize) is pure and gets the bulk of the coverage; resolve()
is exercised with injected tmux/capture fns so no live tmux is needed.
"""

import os
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


def test_busy_hook_survives_idle_scrape_when_shell_still_running():
    """Mid-turn foreground wait: the TUI renders '✻ Cogitated · 1 shell still
    running' over an empty prompt (live capture 2026-08-06), which scrapes as
    idle. A live busy hook must NOT be self-healed away — that was the window
    where a second dispatch could clobber a working worker."""
    r = ws.synthesize(
        hook={"state": "busy", "detail": ""},
        scrape={"state": "idle", "detail": "shell still running", "shells_running": True},
        is_dead=False,
    )
    assert r["state"] == "busy"
    assert r["source"] == "hook(shells-running)"


def test_idle_hook_with_background_shell_stays_idle():
    """Turn really ended (Stop fired) with a long-lived background shell (dev
    server): still idle and dispatchable — the guard is busy-hook-only."""
    r = ws.synthesize(
        hook={"state": "idle", "detail": ""},
        scrape={"state": "idle", "detail": "shell still running", "shells_running": True},
        is_dead=False,
    )
    assert r["state"] == "idle"


def test_no_hook_with_background_shell_stays_idle():
    # invited/legacy worker without hooks: scrape verbatim, flag changes nothing.
    r = ws.synthesize(
        hook=None,
        scrape={"state": "idle", "detail": "shell still running", "shells_running": True},
        is_dead=False,
    )
    assert r["state"] == "idle"


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


def test_hook_awaiting_permission_is_authoritative():
    # PermissionRequest reports the tool prompt directly; scrape sees an idle-
    # looking pane behind the modal and must not override it.
    r = ws.synthesize(
        hook={"state": "awaiting_permission", "detail": "Bash(rm -rf)"},
        scrape={"state": "idle", "detail": ""},
        is_dead=False,
    )
    assert r["state"] == "awaiting_permission"
    assert r["detail"] == "Bash(rm -rf)"
    assert r["source"] == "hook"


def test_hook_ended_reports_dead():
    # SessionEnd leaves a bare shell in the pane, which scrapes as idle — without
    # this the worker would look ready and swallow every dispatch sent to it.
    r = ws.synthesize(
        hook={"state": "ended", "detail": ""},
        scrape={"state": "idle", "detail": ""},
        is_dead=False,
    )
    assert r["state"] == "dead"
    assert r["source"] == "hook"


def test_scrape_cannot_resurrect_an_ended_session():
    r = ws.synthesize(
        hook={"state": "ended", "detail": ""},
        scrape={"state": "busy", "detail": "Whisking…"},
        is_dead=False,
    )
    assert r["state"] == "dead"


def test_rate_limited_still_outranks_ended():
    # A scrape meta-state means the API refused us, which is the more actionable
    # report; liveness/meta precedence must survive the new terminal handling.
    r = ws.synthesize(
        hook={"state": "ended", "detail": ""},
        scrape={"state": "rate_limited", "detail": "5h: 100%"},
        is_dead=False,
    )
    assert r["state"] == "rate_limited"
    assert r["source"] == "scrape-meta"


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


def test_resolve_flags_orphan_when_dead_pane_still_runs_an_agent(tmp_path):
    """A killed worker whose window someone relaunched their own claude in.

    Stays `dead` (nobody answers for that name) but must carry the flag, since
    the cleanup path for a plain dead worker destroys the window.
    """
    _write_registry(tmp_path)
    states = tmp_path / "states"
    states.mkdir()
    (states / "backend.json").write_text(json.dumps({"state": "ended", "detail": ""}))

    r = ws.resolve(
        "backend", tmp_path,
        session_running_fn=lambda s: True,
        capture_fn=lambda t, with_ansi=False: "────────────\n❯ ",
        process_fn=lambda t: "node",  # claude, as macOS reports it
    )
    assert r["state"] == "dead"
    assert r["orphaned"] is True
    assert r["orphan_process"] == "node"
    assert "unregistered agent" in r["detail"]


def test_resolve_dead_with_bare_shell_is_not_orphaned(tmp_path):
    _write_registry(tmp_path)
    states = tmp_path / "states"
    states.mkdir()
    (states / "backend.json").write_text(json.dumps({"state": "ended", "detail": ""}))

    r = ws.resolve(
        "backend", tmp_path,
        session_running_fn=lambda s: True,
        capture_fn=lambda t, with_ansi=False: "$ ",
        process_fn=lambda t: "zsh",
    )
    assert r["state"] == "dead"
    assert "orphaned" not in r


def test_resolve_live_worker_never_probes_for_orphans(tmp_path):
    """The flag is meaningless unless the worker is dead — don't pay for the call."""
    _write_registry(tmp_path)
    calls = []

    r = ws.resolve(
        "backend", tmp_path,
        session_running_fn=lambda s: True,
        capture_fn=lambda t, with_ansi=False: "✻ Whisking…\n────────────\n❯ ",
        process_fn=lambda t: calls.append(t) or "node",
    )
    assert r["state"] == "busy"
    assert calls == []


def test_detect_orphan_process_ignores_shells():
    assert ws.detect_orphan_process("t", process_fn=lambda t: "claude") == "claude"
    assert ws.detect_orphan_process("t", process_fn=lambda t: "2.1.220") == "2.1.220"
    assert ws.detect_orphan_process("t", process_fn=lambda t: "codex") == "codex"
    assert ws.detect_orphan_process("t", process_fn=lambda t: "bash") == ""
    assert ws.detect_orphan_process("t", process_fn=lambda t: "") == ""


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


# ── wait_until ─────────────────────────────────────────────────────────────
def _fake_clock():
    """(monotonic_fn, sleep_fn) pair driven by sleep calls — no real waiting."""
    t = {"now": 0.0}

    def monotonic():
        return t["now"]

    def sleep(sec):
        t["now"] += sec

    return monotonic, sleep


def test_wait_until_reached_after_polling(tmp_path):
    seq = iter([
        {"state": "busy", "target": "t:w"},
        {"state": "busy", "target": "t:w"},
        {"state": "idle", "target": "t:w"},
    ])
    mono, slp = _fake_clock()
    r, outcome = ws.wait_until(
        "w", tmp_path, {"idle"},
        timeout=60, interval=2,
        resolve_fn=lambda w: next(seq), sleep_fn=slp, monotonic_fn=mono,
    )
    assert outcome == "reached"
    assert r["state"] == "idle"


def test_wait_until_accepts_any_of_the_requested_states(tmp_path):
    seq = iter([
        {"state": "busy", "target": "t:w"},
        {"state": "awaiting_permission", "target": "t:w"},
    ])
    mono, slp = _fake_clock()
    r, outcome = ws.wait_until(
        "w", tmp_path, {"idle", "awaiting_permission"},
        timeout=60, interval=2,
        resolve_fn=lambda w: next(seq), sleep_fn=slp, monotonic_fn=mono,
    )
    assert outcome == "reached"
    assert r["state"] == "awaiting_permission"


def test_wait_until_times_out(tmp_path):
    mono, slp = _fake_clock()
    r, outcome = ws.wait_until(
        "w", tmp_path, {"idle"},
        timeout=10, interval=3,
        resolve_fn=lambda w: {"state": "busy", "target": "t:w"},
        sleep_fn=slp, monotonic_fn=mono,
    )
    assert outcome == "timeout"
    assert r["state"] == "busy"


def test_wait_until_exits_early_on_dead_worker(tmp_path):
    calls = {"n": 0}

    def rf(w):
        calls["n"] += 1
        return {"state": "dead", "target": "t:w"}

    mono, slp = _fake_clock()
    r, outcome = ws.wait_until(
        "w", tmp_path, {"idle"},
        timeout=600, interval=2,
        resolve_fn=rf, sleep_fn=slp, monotonic_fn=mono,
    )
    assert outcome == "dead"
    assert calls["n"] == 1  # no pointless polling of a dead worker


def test_wait_until_can_wait_for_dead_itself(tmp_path):
    mono, slp = _fake_clock()
    r, outcome = ws.wait_until(
        "w", tmp_path, {"dead"},
        timeout=60, interval=2,
        resolve_fn=lambda w: {"state": "dead", "target": "t:w"},
        sleep_fn=slp, monotonic_fn=mono,
    )
    assert outcome == "reached"


def test_wait_until_unknown_worker_exits_immediately(tmp_path):
    mono, slp = _fake_clock()
    r, outcome = ws.wait_until(
        "ghost", tmp_path, {"idle"},
        timeout=600, interval=2,
        resolve_fn=lambda w: {"state": "unknown", "target": None},
        sleep_fn=slp, monotonic_fn=mono,
    )
    assert outcome == "unknown"


# ── resolve routes gemini workers to the gemini detector ────────────────────
def test_resolve_gemini_kind_uses_title_signal(tmp_path):
    _write_registry(tmp_path, name="gem", kind="gemini")

    def cap(target, with_ansi=False):
        return "some gemini screen text\n"

    r = ws.resolve(
        "gem", tmp_path,
        session_running_fn=lambda s: True,
        capture_fn=cap,
        title_fn=lambda t: "✦  Working… (repo)",
    )
    assert r["state"] == "busy"
    assert r["kind"] == "gemini"


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


def _states(state_dir, name, payload):
    d = state_dir / "states"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.json").write_text(json.dumps(payload))


def test_hook_overlay_reads_only_the_worker_s_own_team(tmp_path):
    """Worker names repeat across teams. A stale `awaiting_user` left by a
    same-named worker in a team last touched weeks ago used to win, reporting a
    busy worker as waiting for input."""
    from lib.worker_state import annotate_workers_with_hooks

    stale, own = tmp_path / "TTD", tmp_path / "small-star"
    _states(stale, "homepage_backend",
            {"state": "awaiting_user", "detail": "Claude is waiting for your input"})
    _states(own, "homepage_backend", {"state": "busy", "detail": ""})

    worker = {
        "name": "homepage-backend", "state": "busy", "detail": "Newspapering…",
        "state_dir": str(own),
    }

    # Stale dir deliberately searched first — that ordering is what exposed it.
    out = annotate_workers_with_hooks([worker], stale, own)

    assert out[0]["state"] == "busy"


def test_hook_overlay_falls_back_when_the_worker_has_no_team(tmp_path):
    """Discovered panes carry no registration, so they keep the old search."""
    from lib.worker_state import annotate_workers_with_hooks

    sd = tmp_path / "some-team"
    _states(sd, "solo", {"state": "awaiting_user", "detail": "waiting"})
    worker = {"name": "solo", "state": "idle", "detail": ""}

    out = annotate_workers_with_hooks([worker], sd)

    assert out[0]["state"] == "awaiting_user"


def test_heartbeat_lookup_is_scoped_to_the_worker_s_team(tmp_path):
    """Same collision on the liveness layer: a stale heartbeat from another
    team's same-named worker would force a running pane to 'dead'."""
    from lib.heartbeat import annotate_workers

    stale, own = tmp_path / "TTD", tmp_path / "small-star"
    for d, epoch in ((stale, time.time() - 30 * 86400), (own, time.time())):
        hb = d / "heartbeats"
        hb.mkdir(parents=True, exist_ok=True)
        (hb / "homepage_backend.json").write_text(
            json.dumps({"worker": "homepage-backend", "pid": os.getpid(),
                        "last_seen_epoch": epoch})
        )

    worker = {"name": "homepage-backend", "state": "busy", "state_dir": str(own)}

    out = annotate_workers([worker], stale, own)

    assert out[0]["state"] == "busy"


# ── an open modal dialog outranks a stale hook ───────────────────────────────
# The PermissionRequest hook does not reach every build or settings merge —
# state-hook.sh keeps a Notification string-match fallback for exactly that —
# so a worker can sit on an approval dialog while its last hook event still
# says idle (or busy, from the turn that triggered the tool). The screen is
# unambiguous in that situation (the dialog replaces the input prompt), and
# reporting idle would advertise a blocked worker as dispatchable.

def test_open_dialog_overrides_stale_idle_hook():
    r = ws.synthesize(hook={"state": "idle"},
                      scrape={"state": "awaiting_permission", "detail": "Check UI repo branch"},
                      is_dead=False)
    assert r["state"] == "awaiting_permission"


def test_open_dialog_outranks_busy_hook_for_actionability():
    r = ws.synthesize(hook={"state": "busy"},
                      scrape={"state": "awaiting_permission", "detail": "x"},
                      is_dead=False)
    assert r["state"] == "awaiting_permission"


def test_ended_session_still_wins_over_a_dialog_left_on_screen():
    r = ws.synthesize(hook={"state": "ended"},
                      scrape={"state": "awaiting_permission", "detail": "x"},
                      is_dead=False)
    assert r["state"] == "dead"
