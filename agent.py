#!/usr/bin/env python3
"""Per-host agent: scan tmux for LLM CLIs and report to the hub.

Runs as a long-lived process. Usage:

    agent.py --hub-url http://main-pc:8774 --host-id $(hostname)

Or via config file:

    agent.py --config $STATE_DIR/agent.config

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
from lib.heartbeat import annotate_workers  # noqa: E402


@dataclass
class AgentConfig:
    hub_url: str
    host_id: str
    registry_path: Path
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


def _parse_all_registries(main_path: Path) -> dict:
    """Merge main registry with sibling sub-directory registries (worktree teams).

    Layout:
      ~/.darkarchon/<team>/workers-runtime.env             <- main
      ~/.darkarchon/<team>/<sub_a>/workers-runtime.env     <- worktree team
      ~/.darkarchon/<team>/<sub_b>/workers-runtime.env     <- worktree team

    Without this, agent would only resolve workers spawned in its own team
    and the others would show up as 'discovered' with raw target names.
    """
    # Build sub-registries first, then overlay the main one so the hub's
    # own registry wins on key collision. Without this, a stale worktree
    # entry with the same target as a freshly-invited main worker would
    # shadow the new entry (last-wins iteration order).
    sub_merged: dict = {}
    parent = main_path.parent
    if parent.is_dir():
        for sub in parent.iterdir():
            if not sub.is_dir():
                continue
            sub_reg = sub / "workers-runtime.env"
            if not sub_reg.exists():
                continue
            for k, v in parse_registry_file(sub_reg).items():
                sub_merged[k] = v
    main = parse_registry_file(main_path)
    sub_merged.update(main)
    return sub_merged


def _collect_state_dirs(main_path: Path) -> list[Path]:
    """Main state dir + sibling worktree state dirs (anywhere we might
    find a heartbeat file). Mirrors `_parse_all_registries` discovery."""
    out: list[Path] = [main_path.parent]
    parent = main_path.parent.parent
    if parent.is_dir():
        for sub in parent.iterdir():
            if not sub.is_dir():
                continue
            if not (sub / "workers-runtime.env").exists():
                continue
            out.append(sub)
    return out


def report_once(cfg: AgentConfig) -> int:
    registry = _parse_all_registries(cfg.registry_path)
    scanned = scan_panes(allowed_processes=cfg.llm_processes, window_names=cfg.llm_window_names)
    workers = resolve_workers(scanned, registry)
    workers = annotate_workers(workers, *_collect_state_dirs(cfg.registry_path))
    url = f"{cfg.hub_url.rstrip('/')}/api/hosts/{cfg.host_id}/state"
    return _http_post_json(url, {"workers": workers}, timeout=cfg.request_timeout)


def run_loop(cfg: AgentConfig):
    print(
        f"[agent] host_id={cfg.host_id} hub={cfg.hub_url} "
        f"interval={cfg.interval_seconds}s registry={cfg.registry_path}"
    )
    while True:
        try:
            report_once(cfg)
        except Exception as e:
            print(f"[agent] iteration error: {e}", file=sys.stderr)
        time.sleep(cfg.interval_seconds)


def main():
    p = argparse.ArgumentParser()
    _default_config_dir = os.environ.get("STATE_DIR")
    _default_config = Path(_default_config_dir) / "agent.config" if _default_config_dir else None
    p.add_argument("--config", type=Path, default=_default_config)
    p.add_argument("--hub-url", help="overrides config")
    p.add_argument("--host-id", help="overrides config (default: hostname)")
    p.add_argument("--registry", type=Path, help="overrides config (path to workers-runtime.env)")
    p.add_argument("--interval", type=float, help="overrides config (seconds)")
    args = p.parse_args()

    file_cfg = load_config_file(args.config) if args.config else {}

    hub_url = args.hub_url or file_cfg.get("HUB_URL")
    if not hub_url:
        print("ERROR: --hub-url or HUB_URL in config required", file=sys.stderr)
        sys.exit(1)
    host_id = args.host_id or file_cfg.get("HOST_ID") or socket.gethostname()
    default_registry = file_cfg.get("REGISTRY_PATH")
    if not default_registry and _default_config_dir:
        default_registry = str(Path(_default_config_dir) / "workers-runtime.env")
    registry = args.registry or (Path(default_registry) if default_registry else Path("/dev/null"))
    interval = args.interval if args.interval else float(file_cfg.get("INTERVAL", "5"))
    llm_processes = tuple((file_cfg.get("LLM_PROCESSES") or "claude,codex").split(","))
    llm_window_names = tuple((file_cfg.get("LLM_WINDOWS") or "claude").split(","))

    cfg = AgentConfig(
        hub_url=hub_url,
        host_id=host_id,
        registry_path=registry,
        interval_seconds=interval,
        llm_processes=tuple(p.strip() for p in llm_processes if p.strip()),
        llm_window_names=tuple(w.strip() for w in llm_window_names if w.strip()),
    )
    run_loop(cfg)


if __name__ == "__main__":
    main()
