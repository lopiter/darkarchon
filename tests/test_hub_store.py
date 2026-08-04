"""Tests for the hub in-memory host state store."""

from lib.hub_store import HostStateStore


def test_update_host_stores_workers():
    store = HostStateStore(stale_after_seconds=60)
    store.update_host(
        host_id="main-pc",
        workers=[{"name": "app1", "state": "idle"}],
    )
    all_workers = store.get_all_workers()
    assert len(all_workers) == 1
    assert all_workers[0]["host"] == "main-pc"
    assert all_workers[0]["name"] == "app1"


def test_update_host_replaces_previous_workers_for_same_host():
    store = HostStateStore(stale_after_seconds=60)
    store.update_host("main-pc", workers=[{"name": "w1", "state": "idle"}])
    store.update_host("main-pc", workers=[{"name": "w2", "state": "busy"}])
    workers = store.get_all_workers()
    assert len(workers) == 1
    assert workers[0]["name"] == "w2"


def test_multiple_hosts_merge_in_get_all_workers():
    store = HostStateStore(stale_after_seconds=60)
    store.update_host("main-pc", workers=[{"name": "w1", "state": "idle"}])
    store.update_host("remote-pc", workers=[{"name": "w2", "state": "busy"}])
    workers = store.get_all_workers()
    hosts = {w["host"] for w in workers}
    assert hosts == {"main-pc", "remote-pc"}


def test_stale_host_marked_dead_in_get_all_workers(monkeypatch):
    store = HostStateStore(stale_after_seconds=2)
    fake_now = [1000.0]
    monkeypatch.setattr("lib.hub_store.time.time", lambda: fake_now[0])

    store.update_host("remote-pc", workers=[{"name": "w1", "state": "idle"}])
    fake_now[0] += 5  # advance 5 seconds — exceeds stale_after=2
    workers = store.get_all_workers()
    assert all(w["state"] == "dead" for w in workers)


def test_stale_host_still_present_before_evict_window(monkeypatch):
    """Between stale_after and evict_after the host stays, shown as dead."""
    store = HostStateStore(stale_after_seconds=2, evict_after_seconds=300)
    fake_now = [1000.0]
    monkeypatch.setattr("lib.hub_store.time.time", lambda: fake_now[0])

    store.update_host("remote-pc", workers=[{"name": "w1", "state": "idle"}])
    fake_now[0] += 30  # past stale_after(2) but well under evict_after(300)
    workers = store.get_all_workers()
    assert len(workers) == 1
    assert workers[0]["state"] == "dead"
    assert any(h["host_id"] == "remote-pc" for h in store.get_hosts())


def test_host_evicted_after_evict_window(monkeypatch):
    """A host silent past evict_after disappears entirely from store reads."""
    store = HostStateStore(stale_after_seconds=2, evict_after_seconds=300)
    fake_now = [1000.0]
    monkeypatch.setattr("lib.hub_store.time.time", lambda: fake_now[0])

    store.update_host("remote-pc", workers=[{"name": "w1", "state": "idle"}])
    fake_now[0] += 301  # exceeds evict_after=300
    assert store.get_all_workers() == []
    assert store.get_hosts() == []


def test_evicted_host_can_rejoin_fresh(monkeypatch):
    """After eviction, a new POST re-registers the host as fresh (not dead)."""
    store = HostStateStore(stale_after_seconds=2, evict_after_seconds=300)
    fake_now = [1000.0]
    monkeypatch.setattr("lib.hub_store.time.time", lambda: fake_now[0])

    store.update_host("remote-pc", workers=[{"name": "w1", "state": "idle"}])
    fake_now[0] += 301
    assert store.get_all_workers() == []  # evicted

    store.update_host("remote-pc", workers=[{"name": "w1", "state": "idle"}])
    workers = store.get_all_workers()
    assert len(workers) == 1
    assert workers[0]["state"] == "idle"  # fresh, not carried-over dead


def _report(store, state, *, focused=False, host="main-pc", target="x:1"):
    w = {"name": "w1", "state": state, "target": target}
    if focused:
        w["focused"] = True
    return store.update_host(host, workers=[w])


def test_busy_to_idle_records_finished_at(monkeypatch):
    store = HostStateStore(stale_after_seconds=60)
    fake_now = [1000.0]
    monkeypatch.setattr("lib.hub_store.time.time", lambda: fake_now[0])

    _report(store, "busy")
    fake_now[0] = 1010.0
    _report(store, "idle")
    (w,) = store.get_all_workers()
    assert w["finished_at"] == 1010.0
    assert "acked_at" not in w  # nobody has looked yet → unreviewed


def test_compacting_to_idle_also_counts_as_finished(monkeypatch):
    store = HostStateStore(stale_after_seconds=60)
    fake_now = [1000.0]
    monkeypatch.setattr("lib.hub_store.time.time", lambda: fake_now[0])

    _report(store, "busy")
    _report(store, "compacting")
    fake_now[0] = 1020.0
    _report(store, "idle")
    (w,) = store.get_all_workers()
    assert w["finished_at"] == 1020.0


def test_finishing_in_a_continuously_focused_pane_stays_unseen(monkeypatch):
    """tmux keeps the active pane 'focused' even when the terminal app is
    hidden behind another window, so merely-being-focused must NOT ack — the
    user parked on a pane while working in the browser would otherwise never
    see its completions."""
    store = HostStateStore(stale_after_seconds=60)
    fake_now = [1000.0]
    monkeypatch.setattr("lib.hub_store.time.time", lambda: fake_now[0])

    _report(store, "busy", focused=True)
    fake_now[0] = 1010.0
    _report(store, "idle", focused=True)
    (w,) = store.get_all_workers()
    assert w["finished_at"] == 1010.0
    assert "acked_at" not in w


def test_typing_in_the_pane_acks_pending_finish(monkeypatch):
    """Typing into the worker's pane is deliberate engagement — the user is
    right there, responding to what it produced."""
    store = HostStateStore(stale_after_seconds=60)
    fake_now = [1000.0]
    monkeypatch.setattr("lib.hub_store.time.time", lambda: fake_now[0])

    _report(store, "busy", focused=True)
    fake_now[0] = 1010.0
    _report(store, "idle", focused=True)
    fake_now[0] = 1020.0
    _report(store, "typed", focused=True)
    (w,) = store.get_all_workers()
    assert w["acked_at"] == 1020.0


def test_focus_arrival_acks_pending_finish(monkeypatch):
    """Switching TO the pane (focus transition, not steady state) is the
    'I'm looking at it now' signal that marks the result seen."""
    store = HostStateStore(stale_after_seconds=60)
    fake_now = [1000.0]
    monkeypatch.setattr("lib.hub_store.time.time", lambda: fake_now[0])

    _report(store, "busy")
    fake_now[0] = 1010.0
    _report(store, "idle")
    fake_now[0] = 1050.0
    _report(store, "idle", focused=True)  # user switches to the pane
    (w,) = store.get_all_workers()
    assert w["acked_at"] == 1050.0


def test_ack_single_worker_and_ack_all(monkeypatch):
    store = HostStateStore(stale_after_seconds=60)
    fake_now = [1000.0]
    monkeypatch.setattr("lib.hub_store.time.time", lambda: fake_now[0])

    store.update_host("main-pc", workers=[
        {"name": "w1", "state": "busy", "target": "x:1"},
        {"name": "w2", "state": "busy", "target": "x:2"},
    ])
    fake_now[0] = 1010.0
    store.update_host("main-pc", workers=[
        {"name": "w1", "state": "idle", "target": "x:1"},
        {"name": "w2", "state": "idle", "target": "x:2"},
    ])

    assert store.ack("main-pc", "x:1") == 1
    by_name = {w["name"]: w for w in store.get_all_workers()}
    assert "acked_at" in by_name["w1"]
    assert "acked_at" not in by_name["w2"]

    assert store.ack() == 1  # only w2 still had an unreviewed finish
    assert store.ack() == 0  # nothing left → no-op


def test_done_marker_pruned_when_worker_disappears(monkeypatch):
    store = HostStateStore(stale_after_seconds=60)
    fake_now = [1000.0]
    monkeypatch.setattr("lib.hub_store.time.time", lambda: fake_now[0])

    _report(store, "busy")
    _report(store, "idle")
    store.update_host("main-pc", workers=[])  # pane gone
    _report(store, "idle")  # same target rejoins fresh
    (w,) = store.get_all_workers()
    assert "finished_at" not in w


def test_emit_state_change_events():
    """When a worker transitions from busy → idle, store yields an event."""
    store = HostStateStore(stale_after_seconds=60)
    store.update_host("main-pc", workers=[{"name": "w1", "state": "busy", "target": "x:1"}])
    events = list(store.update_host("main-pc", workers=[{"name": "w1", "state": "idle", "target": "x:1"}]))
    assert len(events) == 1
    assert events[0]["type"] == "state_change"
    assert events[0]["from"] == "busy"
    assert events[0]["to"] == "idle"
    assert events[0]["worker"]["name"] == "w1"
