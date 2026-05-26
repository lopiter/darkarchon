"""Simple in-memory SSE pub/sub broker.

Thread-safe. Each subscriber gets a Queue; publish() fans out to all subscribers.
The HTTP handler holds the connection open, blocks on queue.get() with timeout,
and writes SSE-formatted lines back to the client.
"""

from __future__ import annotations

import queue
import threading


class SseBroker:
    def __init__(self):
        self._subs: set[queue.Queue] = set()
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=100)
        with self._lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            self._subs.discard(q)

    def publish(self, event: dict) -> None:
        with self._lock:
            dead = []
            for q in self._subs:
                try:
                    q.put_nowait(event)
                except queue.Full:
                    dead.append(q)
            for q in dead:
                self._subs.discard(q)
