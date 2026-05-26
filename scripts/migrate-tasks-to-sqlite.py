#!/usr/bin/env python3
"""Migrate existing tasks/*.json files into tasks.db.

One-time tool. Recursively finds every `<state_dir>/tasks/*.json` under
`~/.darkarchon/` (or DARKARCHON_STATE_BASE), inserts each into the SQLite
store at the same state_dir.

Idempotent — re-running won't duplicate rows (INSERT OR REPLACE keyed on id).

Usage:
    scripts/migrate-tasks-to-sqlite.py --dry-run    # report counts only
    scripts/migrate-tasks-to-sqlite.py              # actually import
    scripts/migrate-tasks-to-sqlite.py --base ~/.darkarchon/specific-team
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from lib.task_store import TaskStore  # noqa: E402


def find_state_dirs(base: Path) -> list[Path]:
    """A state dir is any directory that contains a tasks/ subdirectory."""
    out = []
    if not base.is_dir():
        return out
    for sub in base.rglob("tasks"):
        if sub.is_dir() and sub.parent.is_dir():
            out.append(sub.parent)
    return out


def migrate_one(state_dir: Path, dry_run: bool) -> tuple[int, int]:
    """Returns (imported, skipped_invalid)."""
    tasks_dir = state_dir / "tasks"
    if not tasks_dir.is_dir():
        return 0, 0
    imported = 0
    skipped = 0
    store = None if dry_run else TaskStore(state_dir / "tasks.db")
    for f in tasks_dir.glob("*.json"):
        try:
            data = json.loads(f.read_text())
        except Exception as e:
            skipped += 1
            print(f"  ! skip {f.name}: {e}", file=sys.stderr)
            continue
        if not data.get("id") or not data.get("worker"):
            skipped += 1
            print(f"  ! skip {f.name}: missing id/worker", file=sys.stderr)
            continue
        if store is not None:
            try:
                store.insert(data)
            except Exception as e:
                skipped += 1
                print(f"  ! insert failed {f.name}: {e}", file=sys.stderr)
                continue
        imported += 1
    return imported, skipped


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base", type=Path,
                   default=Path(os.environ.get("DARKARCHON_STATE_BASE") or (Path.home() / ".darkarchon")),
                   help="state base dir (default: ~/.darkarchon)")
    p.add_argument("--dry-run", action="store_true", help="report what would happen, no writes")
    args = p.parse_args()

    state_dirs = find_state_dirs(args.base)
    if not state_dirs:
        print(f"No state dirs found under {args.base}")
        return

    print(f"Found {len(state_dirs)} state dir(s) under {args.base}\n")
    total_imported = 0
    total_skipped = 0
    for sd in state_dirs:
        imported, skipped = migrate_one(sd, args.dry_run)
        action = "would import" if args.dry_run else "imported"
        print(f"  {sd}  →  {action} {imported}, skipped {skipped}")
        total_imported += imported
        total_skipped += skipped

    print()
    print(f"Total: {total_imported} {'would be imported' if args.dry_run else 'imported'}, {total_skipped} skipped")


if __name__ == "__main__":
    main()
