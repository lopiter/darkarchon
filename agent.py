#!/usr/bin/env python3
"""Per-host agent: scan tmux for LLM CLIs and report to the hub.

Host-scoped, not team-scoped: it scans every pane on the box (`tmux list-panes
-a`) and reports them under one HOST_ID, so exactly one instance should run per
host and its config lives at the state root rather than inside a team dir.

Runs as a long-lived process. Usage:

    agent.py --hub-url http://main-pc:8774 --host-id $(hostname)

Or via config file:

    agent.py --config ~/.darkarchon/agent.config

The config file is shell-style KEY=VALUE; see agent.config.example.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from lib.tmux_scanner import scan_panes  # noqa: E402
from lib.worker_resolver import parse_registry_file, resolve_workers  # noqa: E402
from lib.team_index import build_index, discover_teams  # noqa: E402
from lib.heartbeat import annotate_workers  # noqa: E402
from lib.peer_sessions import annotate_workers_with_peer_names  # noqa: E402
from lib.worker_state import annotate_workers_with_hooks  # noqa: E402


_env_root = os.environ.get("DARKARCHON_STATE_ROOT")
DEFAULT_STATE_ROOT = Path(_env_root).expanduser() if _env_root else Path.home() / ".darkarchon"


@dataclass
class AgentConfig:
    hub_url: str
    host_id: str
    state_root: Path
    llm_processes: tuple[str, ...] = ("claude", "codex")
    llm_window_names: tuple[str, ...] = ("claude",)
    interval_seconds: float = 5.0
    request_timeout: float = 3.0


def load_config_file(path: Path) -> dict:
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip("'").strip('"')
    return out


def _http_post_json(url: str, payload: dict, timeout: float = 3.0) -> int:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status
    except urllib.error.URLError as e:
        print(f"[agent] hub unreachable: {e}", file=sys.stderr)
        return -1


def _discover_state_dirs(root: Path) -> list[Path]:
    """Every team state dir under `root`, oldest registry first.

    Since the agent is host-scoped it has no team of its own — every team on the
    box is equally its business, and taking registry lookup and heartbeat lookup
    from one list is what keeps them from drifting to different depths.

    mtime order lets `_merge_registries` resolve a collision in favour of the
    freshest write: the same tmux target can appear in two teams when an old
    entry was never cleaned up, and the live registration is the one written
    last.
    """
    return [d for d, _name in discover_teams(root, order="mtime")]


def _merge_registries(state_dirs: list[Path]) -> dict:
    """Target-keyed registry entries from every team, later dirs winning."""
    merged: dict = {}
    for d in state_dirs:
        merged.update(parse_registry_file(d / "workers-runtime.env"))
    return merged


def report_once(cfg: AgentConfig) -> int:
    state_dirs = _discover_state_dirs(cfg.state_root)
    registry = _merge_registries(state_dirs)
    # Feed the registry's recorded agent kind into the scanner so it routes each
    # registered worker to the right detector (codex vs claude) — authoritative
    # over process-name/TUI-glyph heuristics, which are fragile (e.g. nvm codex
    # shows as `node`; codex box-drawing `─` looks like a Claude marker).
    known_kinds = {t: m.get("agent_kind", "claude") for t, m in registry.items()}
    scanned = scan_panes(
        allowed_processes=cfg.llm_processes,
        window_names=cfg.llm_window_names,
        known_kinds=known_kinds,
    )
    workers = resolve_workers(scanned, registry)
    # Overlay hook-reported state (event-accurate awaiting_user with the actual
    # permission message) on top of the TUI scrape for spawned Claude workers.
    # Workers without a hook file (invited/codex/legacy) pass through untouched.
    workers = annotate_workers_with_hooks(workers, *state_dirs)
    # Heartbeat liveness has the final say — a dead worker is dead regardless of
    # any stale hook state, so this runs AFTER the hook overlay.
    workers = annotate_workers(workers, *state_dirs)
    # Claude Code's own session registry (~/.claude/sessions) names every live
    # session for cross-session messaging; joining it by pane gives each Claude
    # pane a copyable `peer_name` any local session can SendMessage to.
    workers = annotate_workers_with_peer_names(workers)
    # A host's state dirs are only visible to that host, so the team index has
    # to be built here and carried up rather than read off the hub's own disk —
    # otherwise a remote host's teams have no age at all, and one whose name
    # happens to match a directory on the hub picks up that stranger's age.
    # Tiering stays the hub's call (it owns the thresholds); these are facts.
    teams = build_index(cfg.state_root)
    url = f"{cfg.hub_url.rstrip('/')}/api/hosts/{cfg.host_id}/state"
    return _http_post_json(
        url, {"workers": workers, "teams": teams}, timeout=cfg.request_timeout
    )


def run_loop(cfg: AgentConfig):
    print(
        f"[agent] host_id={cfg.host_id} hub={cfg.hub_url} "
        f"interval={cfg.interval_seconds}s state_root={cfg.state_root}"
    )
    while True:
        try:
            report_once(cfg)
        except Exception as e:
            print(f"[agent] iteration error: {e}", file=sys.stderr)
        time.sleep(cfg.interval_seconds)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", type=Path, default=DEFAULT_STATE_ROOT / "agent.config")
    p.add_argument("--hub-url", help="overrides config")
    p.add_argument("--host-id", help="overrides config (default: hostname)")
    p.add_argument("--state-root", type=Path, help="overrides config (parent of the team state dirs)")
    p.add_argument("--interval", type=float, help="overrides config (seconds)")
    args = p.parse_args()

    file_cfg = load_config_file(args.config) if args.config else {}

    hub_url = args.hub_url or file_cfg.get("HUB_URL")
    if not hub_url:
        print("ERROR: --hub-url or HUB_URL in config required", file=sys.stderr)
        sys.exit(1)
    host_id = args.host_id or file_cfg.get("HOST_ID") or socket.gethostname()
    if file_cfg.get("REGISTRY_PATH"):
        print(
            "[agent] warning: REGISTRY_PATH is no longer read — the agent scans "
            "every team under STATE_ROOT. Remove it from the config.",
            file=sys.stderr,
        )
    cfg_root = file_cfg.get("STATE_ROOT")
    state_root = args.state_root or (Path(cfg_root).expanduser() if cfg_root else DEFAULT_STATE_ROOT)
    interval = args.interval if args.interval else float(file_cfg.get("INTERVAL", "5"))
    llm_processes = tuple((file_cfg.get("LLM_PROCESSES") or "claude,codex").split(","))
    llm_window_names = tuple((file_cfg.get("LLM_WINDOWS") or "claude").split(","))

    cfg = AgentConfig(
        hub_url=hub_url,
        host_id=host_id,
        state_root=state_root,
        interval_seconds=interval,
        llm_processes=tuple(p.strip() for p in llm_processes if p.strip()),
        llm_window_names=tuple(w.strip() for w in llm_window_names if w.strip()),
    )
    run_loop(cfg)


if __name__ == "__main__":
    main()
