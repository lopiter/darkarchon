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
