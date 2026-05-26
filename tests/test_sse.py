"""Tests for the SSE broker — simple in-memory pub/sub for browser notifications."""

import queue

import pytest
from lib.sse import SseBroker


def test_broker_pushes_event_to_subscriber():
    broker = SseBroker()
    sub = broker.subscribe()
    broker.publish({"type": "state_change", "host": "h1"})
    event = sub.get(timeout=1)
    assert event["type"] == "state_change"


def test_broker_multiple_subscribers_each_receive_event():
    broker = SseBroker()
    sub1 = broker.subscribe()
    sub2 = broker.subscribe()
    broker.publish({"type": "state_change"})
    assert sub1.get(timeout=1)["type"] == "state_change"
    assert sub2.get(timeout=1)["type"] == "state_change"


def test_broker_unsubscribe_stops_receiving():
    broker = SseBroker()
    sub = broker.subscribe()
    broker.unsubscribe(sub)
    broker.publish({"type": "state_change"})
    with pytest.raises(queue.Empty):
        sub.get(timeout=0.1)
