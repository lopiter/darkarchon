"""SQLite-backed task store.

Replaces the per-task JSON-file scheme. Single DB file at
`$STATE_DIR/tasks.db`. Sh-callable via:
    python3 lib/task_store.py insert <json_file>
    python3 lib/task_store.py update-status <id> <new_status> [--result …] [--error …]
    python3 lib/task_store.py list [--status …] [--worker …] [--format json|table]
    python3 lib/task_store.py get <id>
    python3 lib/task_store.py prune <days>

State machine: pending → running → {completed,failed,timeout,cancelled}.
Terminal states have no outgoing transitions; an attempted transition raises
ValueError. dispatch.sh + tasks.sh shell out to this module.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

TERMINAL_STATUSES = {'completed', 'failed', 'timeout', 'cancelled'}
ALLOWED_TRANSITIONS = {
    'pending': {'running', 'failed', 'timeout', 'cancelled'},
    'running': {'completed', 'failed', 'timeout', 'cancelled'},
    'completed': set(),
    'failed': set(),
    'timeout': set(),
    'cancelled': set(),
}

SCHEMA = """
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
CREATE INDEX IF NOT EXISTS idx_tasks_worker ON tasks(worker);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_dispatched_at ON tasks(dispatched_at DESC);
"""

FIELDS = [
    'id', 'worker', 'tmux_target', 'prompt', 'prompt_file', 'result_file',
    'done_marker', 'status', 'dispatched_at', 'dispatched_by',
    'started_at', 'completed_at', 'result', 'error',
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


class TaskStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        # WAL + busy_timeout so concurrent dispatches don't lock each other out.
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    def insert(self, task: dict) -> None:
        row = {f: task.get(f) for f in FIELDS}
        if not row['status']:
            row['status'] = 'pending'
        with self._connect() as conn:
            conn.execute(
                f"INSERT OR REPLACE INTO tasks ({','.join(FIELDS)}) "
                f"VALUES ({','.join('?' * len(FIELDS))})",
                [row[f] for f in FIELDS],
            )

    def update_status(self, task_id: str, new_status: str, **extra) -> None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status FROM tasks WHERE id=?", (task_id,)
            ).fetchone()
            if not row:
                raise KeyError(f"task '{task_id}' not found")
            old = row['status']
            if new_status not in ALLOWED_TRANSITIONS.get(old, set()):
                raise ValueError(f"invalid transition: {old} → {new_status}")
            cols = ['status']
            vals = [new_status]
            if new_status == 'running' and 'started_at' not in extra:
                cols.append('started_at')
                vals.append(_now_iso())
            if new_status in TERMINAL_STATUSES and 'completed_at' not in extra:
                cols.append('completed_at')
                vals.append(_now_iso())
            for k, v in extra.items():
                cols.append(k)
                vals.append(v)
            vals.append(task_id)
            conn.execute(
                f"UPDATE tasks SET {','.join(f + '=?' for f in cols)} WHERE id=?",
                vals,
            )

    def get(self, task_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            return dict(row) if row else None

    def list(self, status: str | None = None, worker: str | None = None,
             since: str | None = None, limit: int = 100) -> list[dict]:
        where, params = [], []
        if status:
            where.append('status=?'); params.append(status)
        if worker:
            where.append('worker=?'); params.append(worker)
        if since:
            where.append('dispatched_at>=?'); params.append(since)
        clause = ' WHERE ' + ' AND '.join(where) if where else ''
        sql = f"SELECT * FROM tasks{clause} ORDER BY dispatched_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def last_activity_at(self, worker: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT MAX(COALESCE(completed_at, started_at, dispatched_at)) AS ts "
                "FROM tasks WHERE worker=?",
                (worker,),
            ).fetchone()
            return row['ts'] if row and row['ts'] else None

    def prune_older_than(self, days: int) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%SZ')
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM tasks WHERE dispatched_at < ?", (cutoff,))
            return cur.rowcount


def _resolve_db_path() -> Path:
    state_dir = os.environ.get('STATE_DIR')
    if not state_dir:
        team = os.environ.get('DARKARCHON_TEAM', 'default')
        prefix = os.environ.get('TOOL_PREFIX', 'darkarchon')
        state_dir = str(Path.home() / f'.{prefix}' / team)
    return Path(state_dir) / 'tasks.db'


def _cli() -> None:
    p = argparse.ArgumentParser(prog='task_store', description='darkarchon SQLite task store CLI')
    p.add_argument('--db', type=Path, help='override db path (default: $STATE_DIR/tasks.db)')
    sub = p.add_subparsers(dest='cmd', required=True)

    pi = sub.add_parser('insert', help='insert from json file or stdin')
    pi.add_argument('file', nargs='?', help='json file (default stdin)')

    pu = sub.add_parser('update-status', help='transition status (validated)')
    pu.add_argument('id')
    pu.add_argument('status')
    pu.add_argument('--result', default=None)
    pu.add_argument('--error', default=None)

    pg = sub.add_parser('get', help='print one task as json')
    pg.add_argument('id')

    pl = sub.add_parser('list', help='list tasks (newest first)')
    pl.add_argument('--status', default=None)
    pl.add_argument('--worker', default=None)
    pl.add_argument('--since', default=None, help='ISO timestamp (>=)')
    pl.add_argument('--limit', type=int, default=100)
    pl.add_argument('--format', choices=['json', 'table'], default='table')

    pr = sub.add_parser('prune', help='delete tasks older than N days')
    pr.add_argument('days', type=int)

    pla = sub.add_parser('last-activity', help='print latest activity timestamp for a worker')
    pla.add_argument('worker')

    args = p.parse_args()
    store = TaskStore(args.db or _resolve_db_path())

    if args.cmd == 'insert':
        src = sys.stdin if not args.file else open(args.file)
        store.insert(json.load(src))
    elif args.cmd == 'update-status':
        extra = {}
        if args.result is not None:
            extra['result'] = args.result
        if args.error is not None:
            extra['error'] = args.error
        store.update_status(args.id, args.status, **extra)
    elif args.cmd == 'get':
        print(json.dumps(store.get(args.id) or {}, indent=2))
    elif args.cmd == 'list':
        rows = store.list(status=args.status, worker=args.worker,
                          since=args.since, limit=args.limit)
        if args.format == 'json':
            print(json.dumps(rows, indent=2))
        else:
            print(f"{'ID':<22} {'WORKER':<15} {'STATUS':<12} {'DISPATCHED'}")
            print('-' * 70)
            for r in rows:
                print(f"{r['id']:<22} {(r['worker'] or '')[:15]:<15} {r['status']:<12} {r['dispatched_at']}")
            if not rows:
                print('(no tasks)')
    elif args.cmd == 'prune':
        print(f"deleted {store.prune_older_than(args.days)}")
    elif args.cmd == 'last-activity':
        ts = store.last_activity_at(args.worker)
        if ts:
            print(ts)


if __name__ == '__main__':
    _cli()
