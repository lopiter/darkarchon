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
def running_hub():
    from lib.hub_store import HostStateStore

    import dashboard

    store = HostStateStore(stale_after_seconds=60)
    dashboard.STORE = store  # inject test store

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
