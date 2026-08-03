#!/usr/bin/env python3
"""Backing queries for teams.sh. Reads state dirs directly — no hub required.

Two subcommands, both read-only:
  list     human-readable table, or --json
  select   newline-separated state dirs matching a tier, for the shell to move
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from lib.team_index import build_index  # noqa: E402


def _age(seconds: int | None) -> str:
    if seconds is None:
        return "never"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def _size(nbytes: int) -> str:
    if nbytes >= 1 << 20:
        return f"{nbytes / (1 << 20):.1f}M"
    return f"{nbytes / 1024:.0f}K"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=("list", "select"))
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--dormant-days", type=int, default=7)
    p.add_argument("--stale-days", type=int, default=30)
    p.add_argument("--tier", help="select: only teams in this tier")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    rows = build_index(
        args.root, dormant_days=args.dormant_days, stale_days=args.stale_days
    )

    if args.command == "select":
        for r in rows:
            if args.tier and r["tier"] != args.tier:
                continue
            print(r["state_dir"])
        return 0

    if args.json:
        print(json.dumps(rows, indent=2))
        return 0

    if not rows:
        print(f"no teams under {args.root}")
        return 0

    print(f"{'TEAM':28} {'TIER':8} {'IDLE':>6} {'LAST SIGNAL':12} {'WORKERS':>7} {'SIZE':>6}")
    for r in rows:
        print(
            f"{r['name'][:28]:28} {r['tier']:8} {_age(r['idle_seconds']):>6} "
            f"{str(r['last_activity_source'] or '-'):12} {r['workers']:>7} "
            f"{_size(r['size_bytes']):>6}"
        )
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["tier"]] = counts.get(r["tier"], 0) + 1
    print()
    print("  " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print(f"  thresholds: dormant >{args.dormant_days}d, stale >{args.stale_days}d")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
