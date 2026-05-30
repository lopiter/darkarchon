"""In-memory host state store for the hub.

The dashboard hub keeps no DB — host reports are POSTed in, stored in a dict,
and served back on GET. Stale hosts (no heartbeat for N seconds) have their
workers marked dead; hosts silent past `evict_after_seconds` are dropped
entirely so a long-gone PC disappears from the dashboard instead of
lingering forever (DESIGN.md §7.2 "5분 후 dead 숨김").
"""

from __future__ import annotations

import threading
import time
from typing import Iterable


class HostStateStore:
    def __init__(
        self,
        stale_after_seconds: float = 30.0,
        evict_after_seconds: float = 300.0,
    ):
        self._stale_after = stale_after_seconds
        self._evict_after = evict_after_seconds
        self._hosts: dict[str, dict] = {}  # host_id → {last_seen, workers}
        self._lock = threading.Lock()

    def _evict_expired(self, now: float) -> None:
        """Drop hosts silent past evict_after. Caller must hold the lock."""
        dead = [
            h for h, info in self._hosts.items()
            if (now - info["last_seen"]) > self._evict_after
        ]
        for h in dead:
            del self._hosts[h]

    def update_host(self, host_id: str, workers: list[dict]) -> Iterable[dict]:
        """Replace this host's workers list. Yield events for state transitions."""
        now = time.time()
        with self._lock:
            prev = self._hosts.get(host_id, {}).get("workers", [])
            prev_by_target = {w.get("target"): w for w in prev}
            events: list[dict] = []
            for w in workers:
                tgt = w.get("target")
                prev_state = prev_by_target.get(tgt, {}).get("state")
                cur_state = w.get("state")
                if prev_state and prev_state != cur_state:
                    events.append(
                        {
                            "type": "state_change",
                            "host": host_id,
                            "worker": w,
                            "from": prev_state,
                            "to": cur_state,
                        }
                    )
            self._hosts[host_id] = {"last_seen": now, "workers": list(workers)}
            return events

    def get_all_workers(self) -> list[dict]:
        """Return flattened worker list with `host` field added. Stale hosts → state=dead."""
        now = time.time()
        out: list[dict] = []
        with self._lock:
            self._evict_expired(now)
            for host_id, info in self._hosts.items():
                is_stale = (now - info["last_seen"]) > self._stale_after
                for w in info["workers"]:
                    decorated = {**w, "host": host_id, "host_last_seen": info["last_seen"]}
                    if is_stale:
                        decorated["state"] = "dead"
                        decorated["detail"] = f"host stale ({int(now - info['last_seen'])}s)"
                    out.append(decorated)
        return out

    def get_hosts(self) -> list[dict]:
        """Return host-level summary."""
        now = time.time()
        with self._lock:
            self._evict_expired(now)
            return [
                {
                    "host_id": h,
                    "last_seen": info["last_seen"],
                    "worker_count": len(info["workers"]),
                    "stale": (now - info["last_seen"]) > self._stale_after,
                }
                for h, info in self._hosts.items()
            ]
