"""Tests for the SQLite task store (lib/task_store.py).

Covers the two things a schema change can break — migrating a DB created by an
older release, and doing so while another dispatch opens the same file — plus
the circuit-breaker query and the transition validator.
"""

import sqlite3
import threading
import traceback

import pytest

from lib.task_store import MIGRATIONS, SCHEMA, TaskStore

# The 14-column table as it shipped before `attempt`/`deps` existed. Tests that
# migrate start from this so we're exercising the real upgrade path.
LEGACY_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    worker TEXT NOT NULL,
    tmux_target TEXT NOT NULL,
    prompt TEXT NOT NULL,
    prompt_file TEXT,
    result_file TEXT,
    done_marker TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    dispatched_at TEXT NOT NULL,
    dispatched_by TEXT,
    started_at TEXT,
    completed_at TEXT,
    result TEXT,
    error TEXT
);
"""


def _task(tid, worker="backend", status="pending", dispatched_at="2026-07-29T08:00:00Z", **kw):
    row = {
        "id": tid,
        "worker": worker,
        "tmux_target": "team:backend",
        "prompt": "do the thing",
        "status": status,
        "dispatched_at": dispatched_at,
    }
    row.update(kw)
    return row


def _columns(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}
    finally:
        conn.close()


# ── migration ──────────────────────────────────────────────────────────────
def test_fresh_db_has_new_columns(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    cols = _columns(store.db_path)
    for name, _ in MIGRATIONS:
        assert name in cols


def test_legacy_db_gains_columns_and_keeps_rows(tmp_path):
    db = tmp_path / "tasks.db"
    conn = sqlite3.connect(db)
    conn.executescript(LEGACY_SCHEMA)
    conn.execute(
        "INSERT INTO tasks (id, worker, tmux_target, prompt, status, dispatched_at) "
        "VALUES ('old-1', 'backend', 'team:backend', 'legacy', 'completed', '2026-07-01T00:00:00Z')"
    )
    conn.commit()
    conn.close()

    store = TaskStore(db)
    assert {"attempt", "deps"} <= _columns(db)
    # Pre-existing rows survive and read back with the new columns defaulted.
    old = store.get("old-1")
    assert old["prompt"] == "legacy"
    assert old["attempt"] == 1
    assert old["deps"] == "[]"


def test_insert_works_after_migration(tmp_path):
    """The regression that motivated migrations: INSERT names every column in
    FIELDS, so a legacy table without them fails outright."""
    db = tmp_path / "tasks.db"
    conn = sqlite3.connect(db)
    conn.executescript(LEGACY_SCHEMA)
    conn.commit()
    conn.close()

    store = TaskStore(db)
    store.insert(_task("new-1"))
    assert store.get("new-1")["attempt"] == 1


def test_migration_is_idempotent(tmp_path):
    db = tmp_path / "tasks.db"
    TaskStore(db)
    TaskStore(db)  # would raise "duplicate column name" if unguarded
    assert {"attempt", "deps"} <= _columns(db)


def test_concurrent_open_of_legacy_db(tmp_path):
    """Two dispatches racing to migrate: PRAGMA-then-ALTER is not atomic, so the
    loser must swallow "duplicate column name" rather than crash."""
    db = tmp_path / "tasks.db"
    conn = sqlite3.connect(db)
    conn.executescript(LEGACY_SCHEMA)
    conn.commit()
    conn.close()

    errors = []
    barrier = threading.Barrier(2)

    def open_store():
        try:
            barrier.wait(timeout=5)
            TaskStore(db)
        except Exception:  # noqa: BLE001 — the assertion is "nothing escaped"
            errors.append(traceback.format_exc())

    threads = [threading.Thread(target=open_store) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert errors == []
    assert {"attempt", "deps"} <= _columns(db)


def test_migrate_reraises_unexpected_operational_error(tmp_path):
    """Only duplicate-column is benign; a real failure must not be silenced."""
    db = tmp_path / "tasks.db"
    TaskStore(db)  # create the table

    class Boom(sqlite3.Connection):
        pass

    conn = sqlite3.connect(db)
    try:
        # A table_info that reports nothing forces an ALTER on a locked/bad name.
        conn.execute("ALTER TABLE tasks RENAME TO tasks_moved")
        conn.commit()
        with pytest.raises(sqlite3.OperationalError):
            TaskStore._migrate(conn)
    finally:
        conn.close()


# ── insert defaults ────────────────────────────────────────────────────────
def test_insert_defaults_attempt_and_deps(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    store.insert(_task("t1"))
    row = store.get("t1")
    assert row["attempt"] == 1
    assert row["deps"] == "[]"


def test_insert_honours_explicit_values(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    store.insert(_task("t1", attempt=2, deps='["a","b"]'))
    row = store.get("t1")
    assert row["attempt"] == 2
    assert row["deps"] == '["a","b"]'


# ── set_attempt ────────────────────────────────────────────────────────────
def test_set_attempt_leaves_status_alone(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    store.insert(_task("t1"))
    store.update_status("t1", "running")
    store.set_attempt("t1", 2)
    row = store.get("t1")
    assert row["attempt"] == 2
    assert row["status"] == "running"  # running → running would be rejected


def test_set_attempt_unknown_id(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    with pytest.raises(KeyError):
        store.set_attempt("nope", 2)


# ── transition validator (unchanged behaviour, guarded) ────────────────────
def test_terminal_status_rejects_further_updates(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    store.insert(_task("t1"))
    store.update_status("t1", "running")
    store.update_status("t1", "completed", result="ok")
    with pytest.raises(ValueError):
        store.update_status("t1", "failed")


def test_update_status_unknown_id(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    with pytest.raises(KeyError):
        store.update_status("nope", "running")


# ── consecutive_failures (circuit breaker) ─────────────────────────────────
def _settle(store, tid, status, at):
    store.insert(_task(tid, dispatched_at=at))
    store.update_status(tid, "running")
    store.update_status(tid, status)


def test_no_history_is_zero(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    assert store.consecutive_failures("backend") == 0


def test_counts_failures_since_last_success(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    _settle(store, "t1", "completed", "2026-07-29T08:00:01Z")
    _settle(store, "t2", "failed", "2026-07-29T08:00:02Z")
    _settle(store, "t3", "timeout", "2026-07-29T08:00:03Z")
    assert store.consecutive_failures("backend") == 2


def test_success_resets_the_streak(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    _settle(store, "t1", "failed", "2026-07-29T08:00:01Z")
    _settle(store, "t2", "failed", "2026-07-29T08:00:02Z")
    _settle(store, "t3", "completed", "2026-07-29T08:00:03Z")
    assert store.consecutive_failures("backend") == 0


def test_running_and_pending_neither_count_nor_reset(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    _settle(store, "t1", "failed", "2026-07-29T08:00:01Z")
    _settle(store, "t2", "failed", "2026-07-29T08:00:02Z")
    store.insert(_task("t3", dispatched_at="2026-07-29T08:00:03Z"))  # pending
    store.insert(_task("t4", dispatched_at="2026-07-29T08:00:04Z"))
    store.update_status("t4", "running")
    assert store.consecutive_failures("backend") == 2


def test_cancelled_is_ignored(tmp_path):
    """An operator abort is not the worker's fault: it must not count as a
    failure, and it must not mask the older failures behind it."""
    store = TaskStore(tmp_path / "tasks.db")
    _settle(store, "t1", "failed", "2026-07-29T08:00:01Z")
    _settle(store, "t2", "cancelled", "2026-07-29T08:00:02Z")
    assert store.consecutive_failures("backend") == 1


def test_per_worker_isolation(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    _settle(store, "t1", "failed", "2026-07-29T08:00:01Z")
    store.insert(_task("t2", worker="frontend", dispatched_at="2026-07-29T08:00:02Z"))
    store.update_status("t2", "running")
    store.update_status("t2", "failed")
    assert store.consecutive_failures("backend") == 1
    assert store.consecutive_failures("frontend") == 1


def test_same_second_ties_are_ordered_by_id(tmp_path):
    """dispatched_at has one-second resolution; without the id tiebreak the
    newest-first walk would be arbitrary and the streak could flip."""
    store = TaskStore(tmp_path / "tasks.db")
    ts = "2026-07-29T08:00:01Z"
    _settle(store, "20260729-080001-aaaa", "completed", ts)
    _settle(store, "20260729-080001-bbbb", "failed", ts)
    assert store.consecutive_failures("backend") == 1


def test_retry_within_one_dispatch_counts_once(tmp_path):
    """A nudge bumps `attempt` on a single row, so a task that eventually
    succeeded contributes one `completed` — not one entry per attempt."""
    store = TaskStore(tmp_path / "tasks.db")
    store.insert(_task("t1"))
    store.update_status("t1", "running")
    store.set_attempt("t1", 2)
    store.update_status("t1", "completed", result="ok")
    assert store.consecutive_failures("backend") == 0


def test_limit_bounds_the_scan(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    for i in range(5):
        _settle(store, f"t{i}", "failed", f"2026-07-29T08:00:0{i}Z")
    assert store.consecutive_failures("backend", limit=3) == 3
