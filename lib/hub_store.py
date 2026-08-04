"""In-memory host state store for the hub.

The dashboard hub keeps no DB — host reports are POSTed in, stored in a dict,
and served back on GET. Stale hosts (no heartbeat for N seconds) have their
workers marked dead; hosts silent past `evict_after_seconds` are dropped
entirely so a long-gone PC disappears from the dashboard instead of
lingering forever (DESIGN.md §7.2 "hide dead workers after 5 min").
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
        # (host_id, target) → {"finished_at": epoch, "acked_at": epoch}.
        # Unread-result tracking: `finished_at` stamps the last busy→idle
        # transition, `acked_at` the last time the user demonstrably saw the
        # worker (focused pane, detail panel, explicit ack). The UI shows
        # "done, unreviewed" while finished_at > acked_at. Kept separate from
        # the worker dicts because those are replaced wholesale on every host
        # report and this has to survive across reports.
        self._done: dict[tuple[str, str], dict] = {}
        self._lock = threading.Lock()

    def _evict_expired(self, now: float) -> None:
        """Drop hosts silent past evict_after. Caller must hold the lock."""
        dead = [
            h for h, info in self._hosts.items()
            if (now - info["last_seen"]) > self._evict_after
        ]
        for h in dead:
            del self._hosts[h]
            for k in [k for k in self._done if k[0] == h]:
                del self._done[k]

    def update_host(
        self, host_id: str, workers: list[dict], teams: list[dict] | None = None
    ) -> Iterable[dict]:
        """Replace this host's workers (and team index) list.

        Yields events for state transitions. `teams` is each host's own view of
        its state dirs — only that host can see them, so it is stored per host
        rather than merged into a global table. Absent for agents predating the
        team index, which simply contribute no teams.
        """
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
                if tgt:
                    key = (host_id, tgt)
                    if prev_state in ("busy", "compacting") and cur_state == "idle":
                        self._done.setdefault(key, {})["finished_at"] = now
                    # A focused pane is being looked at right now — anything it
                    # finished is seen the moment it happens (mirrors the
                    # notify-watcher rule: no alert for the pane you watch).
                    if w.get("focused") and key in self._done:
                        self._done[key]["acked_at"] = now
            # Registry replacement is wholesale per host; done-markers for panes
            # that no longer report would otherwise leak forever.
            targets = {w.get("target") for w in workers}
            for k in [k for k in self._done if k[0] == host_id and k[1] not in targets]:
                del self._done[k]
            self._hosts[host_id] = {
                "last_seen": now,
                "workers": list(workers),
                "teams": list(teams or []),
            }
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
                    done = self._done.get((host_id, w.get("target")))
                    if done and "finished_at" in done:
                        decorated["finished_at"] = done["finished_at"]
                        if "acked_at" in done:
                            decorated["acked_at"] = done["acked_at"]
                    out.append(decorated)
        return out

    def ack(self, host_id: str | None = None, target: str | None = None) -> int:
        """Mark finished work as seen. Specific worker, or everything when
        called with no arguments. Returns how many markers were acked.

        Only workers with a recorded finish are touched — acking a worker that
        never finished anything is a no-op, so the endpoint can be called
        liberally (every detail-panel open) without growing state.
        """
        now = time.time()
        n = 0
        with self._lock:
            for (h, t), done in self._done.items():
                if host_id is not None and h != host_id:
                    continue
                if target is not None and t != target:
                    continue
                if done.get("finished_at", 0) > done.get("acked_at", 0):
                    done["acked_at"] = now
                    n += 1
        return n

    def get_all_teams(self) -> list[dict]:
        """Every host's team index rows, each stamped with its host.

        A team name is only unique within a host — two machines can both have a
        `voc` state dir with nothing to do with each other — so the host is
        carried through and callers must key on the pair.
        """
        now = time.time()
        out: list[dict] = []
        with self._lock:
            self._evict_expired(now)
            for host_id, info in self._hosts.items():
                for t in info.get("teams", []):
                    out.append({**t, "host": host_id})
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
