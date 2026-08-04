"""End-to-end test for dashboard hub POST endpoint.

Spins up the actual Handler on an ephemeral port via threading.HTTPServer,
makes real HTTP requests against it. Avoids deep mocking of http.server.
"""

import json
import socket
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def running_hub(tmp_path, monkeypatch):
    from lib.hub_store import HostStateStore

    import dashboard

    store = HostStateStore(stale_after_seconds=60)
    dashboard.STORE = store  # inject test store
    # Point the hub's own disk-reading paths at an empty dir. Without this the
    # developer's real ~/.darkarchon leaks in and assigns teams from whatever
    # registries and task history happen to be on the machine running the test.
    monkeypatch.setattr(dashboard, "STATE_DIR", tmp_path / "team")
    monkeypatch.setattr(dashboard, "STATE_ROOT", tmp_path)

    port = _free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), dashboard.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}", store
    server.shutdown()


def test_post_host_state_stores_workers(running_hub):
    url_base, store = running_hub
    payload = {"workers": [{"target": "x:0.0", "name": "w1", "state": "idle", "process": "claude", "cwd": "/"}]}
    req = urllib.request.Request(
        f"{url_base}/api/hosts/main-pc/state",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        body = json.loads(resp.read())
    assert body["accepted"] is True
    workers = store.get_all_workers()
    assert len(workers) == 1
    assert workers[0]["host"] == "main-pc"


def test_get_status_returns_workers_from_all_hosts(running_hub):
    url_base, _store = running_hub
    for host, target in [("main-pc", "x:0.0"), ("remote-pc", "y:0.0")]:
        payload = {
            "workers": [{"target": target, "name": f"w-{host}", "state": "idle", "process": "claude", "cwd": "/"}]
        }
        req = urllib.request.Request(
            f"{url_base}/api/hosts/{host}/state",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req).read()

    with urllib.request.urlopen(f"{url_base}/api/status") as resp:
        data = json.loads(resp.read())
    hosts = {w["host"] for w in data["workers"]}
    assert hosts == {"main-pc", "remote-pc"}


def _post(url_base, host, payload):
    req = urllib.request.Request(
        f"{url_base}/api/hosts/{host}/state",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def _team(name, **over):
    row = {
        "name": name,
        "state_dir": f"/s/{name}",
        "workers": 2,
        "last_activity_at": "2026-06-01T00:00:00Z",
        "last_activity_source": "dispatch",
        "idle_seconds": 0,
        "tier": "stale",
        "size_bytes": 1024,
    }
    row.update(over)
    return row


def test_each_host_reports_its_own_team_index(running_hub):
    """Only a host can see its own state dirs, so the index travels with the
    report rather than being read off the hub's disk."""
    url_base, _store = running_hub
    _post(url_base, "alpha", {"workers": [], "teams": [_team("voc")]})
    _post(url_base, "beta", {"workers": [], "teams": [_team("perf")]})

    with urllib.request.urlopen(f"{url_base}/api/teams") as resp:
        teams = json.loads(resp.read())["teams"]

    assert {(t["host"], t["name"]) for t in teams} == {("alpha", "voc"), ("beta", "perf")}


def test_same_team_name_on_two_hosts_keeps_its_own_age(running_hub):
    """The bug this fixes: a team name matching a directory on another machine
    used to inherit that stranger's age."""
    url_base, _store = running_hub
    _post(url_base, "alpha", {"workers": [], "teams": [_team("dark", last_activity_at="2026-08-01T00:00:00Z")]})
    _post(url_base, "beta", {"workers": [], "teams": [_team("dark", last_activity_at="2026-01-01T00:00:00Z")]})

    with urllib.request.urlopen(f"{url_base}/api/teams") as resp:
        teams = {(t["host"], t["name"]): t for t in json.loads(resp.read())["teams"]}

    assert teams[("alpha", "dark")]["idle_seconds"] < teams[("beta", "dark")]["idle_seconds"]


def test_a_teamless_report_contributes_no_teams(running_hub):
    """Agents predating the team index simply report none."""
    url_base, _store = running_hub
    _post(url_base, "legacy", {"workers": []})

    with urllib.request.urlopen(f"{url_base}/api/teams") as resp:
        assert json.loads(resp.read())["teams"] == []


def test_liveness_is_scoped_to_the_reporting_host(running_hub):
    """A live worker on one host must not mark the same-named team live on
    another."""
    url_base, _store = running_hub
    _post(url_base, "alpha", {
        "workers": [{"target": "voc:1.1", "name": "w", "state": "idle",
                     "process": "claude", "cwd": "/"}],
        "teams": [_team("voc")],
    })
    _post(url_base, "beta", {"workers": [], "teams": [_team("voc")]})

    with urllib.request.urlopen(f"{url_base}/api/status") as resp:
        teams = {(t["host"], t["name"]): t for t in json.loads(resp.read())["teams"]}

    assert teams[("alpha", "voc")]["tier"] == "live"
    assert teams[("beta", "voc")]["tier"] != "live"


def _ack(url_base, body):
    req = urllib.request.Request(
        f"{url_base}/api/ack",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def _worker(state, target="x:1.1"):
    return {"target": target, "name": "w1", "state": state, "process": "claude", "cwd": "/"}


def test_ack_endpoint_clears_unreviewed_finish(running_hub):
    url_base, store = running_hub
    _post(url_base, "main-pc", {"workers": [_worker("busy")]})
    _post(url_base, "main-pc", {"workers": [_worker("idle")]})
    (w,) = store.get_all_workers()
    assert "finished_at" in w and "acked_at" not in w

    assert _ack(url_base, {"host": "main-pc", "target": "x:1.1"})["acked"] == 1
    (w,) = store.get_all_workers()
    assert w["acked_at"] >= w["finished_at"]


def test_ack_all_and_validation(running_hub):
    url_base, store = running_hub
    _post(url_base, "main-pc", {"workers": [_worker("busy")]})
    _post(url_base, "main-pc", {"workers": [_worker("idle")]})

    # Missing host/target without all → 400.
    import urllib.error
    try:
        _ack(url_base, {"host": "main-pc"})
        raise AssertionError("expected 400")
    except urllib.error.HTTPError as e:
        assert e.code == 400

    assert _ack(url_base, {"all": True})["acked"] == 1
    assert _ack(url_base, {"all": True})["acked"] == 0  # idempotent
