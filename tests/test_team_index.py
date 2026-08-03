"""Team discovery and activity aging."""

import json
import os
import time

from lib.team_index import (
    build_index,
    classify,
    discover_teams,
    team_activity,
)

DAY = 86400


def _team(root, *parts, mtime=None):
    d = root.joinpath(*parts)
    d.mkdir(parents=True, exist_ok=True)
    reg = d / "workers-runtime.env"
    reg.write_text("WORKER_a_NAME=a\nWORKER_a_TARGET=s:1\n")
    if mtime is not None:
        os.utime(reg, (mtime, mtime))
    return d


def test_discovers_flat_and_nested_teams(tmp_path):
    _team(tmp_path, "alpha")
    _team(tmp_path, "beta")
    _team(tmp_path, "beta", "feature-x")
    (tmp_path / "no-registry").mkdir()
    (tmp_path / "agent.config").write_text("HUB_URL=http://x\n")

    assert dict(discover_teams(tmp_path)) == {
        tmp_path / "alpha": "alpha",
        tmp_path / "beta": "beta",
        tmp_path / "beta" / "feature-x": "beta-feature-x",
    }


def test_mtime_order_puts_freshest_last(tmp_path):
    _team(tmp_path, "old", mtime=1_000_000)
    _team(tmp_path, "new", mtime=2_000_000)

    ordered = [name for _d, name in discover_teams(tmp_path, order="mtime")]
    assert ordered == ["old", "new"]


def test_missing_root_is_not_an_error(tmp_path):
    assert discover_teams(tmp_path / "nope") == []


def test_activity_takes_newest_signal_and_names_it(tmp_path):
    d = _team(tmp_path, "alpha", mtime=time.time() - 30 * DAY)
    hb = d / "heartbeats"
    hb.mkdir()
    recent = time.time() - 60
    (hb / "w.json").write_text(json.dumps({"last_seen_epoch": recent}))

    act = team_activity(d)
    assert act["last_activity_source"] == "heartbeat"
    assert abs(act["last_activity_epoch"] - recent) < 1


def test_activity_reports_registry_when_nothing_else_happened(tmp_path):
    """A team that was spawned but never given work must be distinguishable
    from one that ran dispatches and then went quiet."""
    stamp = time.time() - 3 * DAY
    d = _team(tmp_path, "alpha", mtime=stamp)

    act = team_activity(d)
    assert act["last_activity_source"] == "registry"
    assert act["signals"]["dispatch"] is None


def test_activity_does_not_create_tasks_db(tmp_path):
    """Asking a dormant team when it last worked must not resurrect it —
    TaskStore creates the db and its schema on construction."""
    d = _team(tmp_path, "alpha")

    team_activity(d)

    assert not (d / "tasks.db").exists()


def test_classify_tiers():
    assert classify(0, is_live=True, worker_count=1) == "live"
    assert classify(3 * DAY, is_live=False, worker_count=1) == "recent"
    assert classify(10 * DAY, is_live=False, worker_count=1) == "dormant"
    assert classify(90 * DAY, is_live=False, worker_count=1) == "stale"
    # No registered workers at all: torn down cleanly, not abandoned.
    assert classify(90 * DAY, is_live=False, worker_count=0) == "empty"
    # Live beats every age.
    assert classify(90 * DAY, is_live=True, worker_count=1) == "live"


def test_classify_honours_custom_thresholds():
    assert classify(3 * DAY, is_live=False, worker_count=1, dormant_days=1) == "dormant"
    assert classify(3 * DAY, is_live=False, worker_count=1, dormant_days=1, stale_days=2) == "stale"


def test_build_index_sorts_most_recent_first(tmp_path):
    now = time.time()
    _team(tmp_path, "ancient", mtime=now - 60 * DAY)
    _team(tmp_path, "fresh", mtime=now - 1 * DAY)
    _team(tmp_path, "middle", mtime=now - 10 * DAY)

    rows = build_index(tmp_path, now=now)

    assert [r["name"] for r in rows] == ["fresh", "middle", "ancient"]
    assert [r["tier"] for r in rows] == ["recent", "dormant", "stale"]


def test_build_index_marks_reported_teams_live(tmp_path):
    now = time.time()
    _team(tmp_path, "quiet", mtime=now - 60 * DAY)

    rows = build_index(tmp_path, live_teams={"quiet"}, now=now)

    assert rows[0]["tier"] == "live"


def test_build_index_counts_workers_and_size(tmp_path):
    d = _team(tmp_path, "alpha")
    (d / "workers-runtime.env").write_text(
        "WORKER_a_TARGET=s:1\nWORKER_b_TARGET=s:2\nWORKER_c_TARGET=s:3\n"
    )

    row = build_index(tmp_path)[0]

    assert row["workers"] == 3
    assert row["size_bytes"] > 0
