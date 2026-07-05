#!/usr/bin/env python3
"""Single source of truth for "what state is worker X in?".

Historically three copies of busy/idle detection existed and drifted apart:
dispatch-safe.sh (shell regex), check-worker-state.sh (shell regex requiring the
✽ glyph), and detectors/claude.py (tested Python, observer side). This module
collapses them to one, layered by signal reliability:

  1. Liveness    — tmux session/window gone, or heartbeat pid dead/stale → dead.
  2. Hook events — $STATE_DIR/states/<safe>.json, authoritative WHILE the
                   worker's heartbeat is alive. Event-driven (Claude Code hooks),
                   no TUI scraping. Only spawned Claude workers have these.
  3. Scrape      — tmux capture + detectors/{claude,codex}.py. Fallback for
                   invited / codex / legacy workers that have no hook file.

`synthesize()` is a pure function over (hook, scrape, is_dead) so the merge
policy is unit-testable without tmux. `resolve()` wires in the real tmux +
registry. Shell callers use the CLI (`--field state`, `--json`, or KEY=VALUE).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_ROOT = HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from lib.detectors.claude import classify_claude_state  # noqa: E402
from lib.detectors.codex import classify_codex_state  # noqa: E402
from lib.heartbeat import (  # noqa: E402
    HEARTBEAT_STALE_SEC,
    heartbeat_age_sec,
    is_pid_alive,
    read_heartbeat,
)
from lib.tmux_scanner import capture_pane  # noqa: E402
from lib.worker_resolver import parse_registry_file  # noqa: E402

# Canonical state vocabulary emitted by this resolver.
#   dead          — worker gone (no session / heartbeat stale / pid dead)
#   rate_limited  — API limit reached (scrape meta-state)
#   error         — codex auth/stream failure (scrape meta-state)
#   awaiting_user — permission prompt / explicit question (hook, rich detail)
#   compacting    — /compact in progress
#   busy          — actively processing a turn
#   unsent        — user has typed but unsent input on the prompt line (scrape only)
#   idle          — at an empty prompt, ready for dispatch
#   unknown       — no recognizable signal
_SCRAPE_META = ("rate_limited", "error")


def _sanitize(name: str) -> str:
    """Same scheme as safe_name() in lib/_lib.sh and heartbeat.py."""
    return "".join(c if c.isalnum() or c == "_" else "_" for c in name)


# ── Hook state file ────────────────────────────────────────────────────────
def read_hook_state(state_dir: Path, worker_name: str) -> dict | None:
    """Read $STATE_DIR/states/<safe>.json written by lib/state-hook.sh.

    Returns {"state", "detail", "ts_epoch", "event"} or None if absent/corrupt.
    Freshness (is the worker still alive?) is decided by the liveness layer in
    resolve(), NOT here — this is a dumb reader.
    """
    f = Path(state_dir) / "states" / f"{_sanitize(worker_name)}.json"
    if not f.exists():
        return None
    try:
        d = json.loads(f.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or "state" not in d:
        return None
    return {
        "state": d.get("state", "unknown"),
        "detail": d.get("detail", "") or "",
        "ts_epoch": d.get("ts_epoch"),
        "event": d.get("event", ""),
    }


# ── Scrape (TUI capture + detector) ────────────────────────────────────────
def _normalize_scrape_state(state: str) -> str:
    # detectors/claude.py names the typed-but-unsent case "typed"; the resolver
    # (and dispatch policy) call it "unsent" to avoid clashing with the
    # dashboard's awaiting_user:typed concept.
    return "unsent" if state == "typed" else state


def scrape_state(target: str, kind: str, capture_fn=capture_pane) -> dict:
    """Capture the pane and classify via the kind-appropriate detector."""
    plain = capture_fn(target, with_ansi=False)
    ansi = capture_fn(target, with_ansi=True)
    if not plain and not ansi:
        return {"state": "unknown", "detail": "no pane capture"}
    if kind == "codex":
        st = classify_codex_state(plain, ansi)
    else:
        st = classify_claude_state(plain, ansi)
    return {"state": _normalize_scrape_state(st["state"]), "detail": st.get("detail", "")}


# ── Pure merge policy ──────────────────────────────────────────────────────
def synthesize(hook: dict | None, scrape: dict, is_dead: bool) -> dict:
    """Combine the three layers into one {state, detail, source}. Pure.

    Precedence:
      - is_dead wins over everything.
      - scrape meta-states (rate_limited, error) always honored: hooks can't
        emit them and they mean the worker can't progress.
      - no hook (invited/codex/legacy) → scrape verbatim.
      - hook says busy but scrape sees idle/unsent → trust scrape. A missed Stop
        hook would otherwise pin the worker busy forever; scrape self-heals it.
      - hook says idle but scrape sees unsent → user is typing (hooks can't see
        the prompt line) → unsent.
      - otherwise the hook is authoritative.
    """
    if is_dead:
        return {"state": "dead", "detail": "", "source": "liveness"}
    if scrape.get("state") in _SCRAPE_META:
        return {**scrape, "source": "scrape-meta"}
    if hook is None:
        return {**scrape, "source": "scrape"}
    h, s = hook["state"], scrape["state"]
    if h == "busy" and s in ("idle", "unsent"):
        return {**scrape, "source": "scrape(hook-stale)"}
    if h == "idle" and s == "unsent":
        return {**scrape, "source": "scrape-overlay"}
    return {"state": h, "detail": hook.get("detail", ""), "source": "hook"}


# ── Liveness ───────────────────────────────────────────────────────────────
def liveness_is_dead(
    state_dir: Path,
    worker_name: str,
    session_running: bool,
    now: float | None = None,
) -> tuple[bool, str]:
    """(is_dead, reason). Session gone, or heartbeat present and stale/pid-dead."""
    if not session_running:
        return True, "tmux session not running"
    hb = read_heartbeat(state_dir, worker_name)
    if hb is not None:
        age = heartbeat_age_sec(hb, now)
        if age is not None and age > HEARTBEAT_STALE_SEC:
            return True, f"heartbeat stale ({int(age)}s)"
        pid = hb.get("pid")
        if isinstance(pid, int) and not is_pid_alive(pid):
            return True, "worker pid gone"
    return False, ""


# ── Registry lookup ────────────────────────────────────────────────────────
def lookup_worker(state_dir: Path, worker_name: str) -> dict | None:
    """Resolve target/kind/cwd for a worker NAME from workers-runtime.env.

    parse_registry_file keys by target; we invert to a name lookup so callers
    pass the human name. Returns {"target","kind","cwd"} or None if unknown.
    """
    registry = parse_registry_file(Path(state_dir) / "workers-runtime.env")
    for target, meta in registry.items():
        if meta.get("name") == worker_name:
            return {"target": target, "kind": meta.get("agent_kind", "claude"), "cwd": meta.get("cwd", "")}
    return None


# ── tmux helpers (real implementations, injectable for tests) ──────────────
def _tmux_session_running(session: str) -> bool:
    import subprocess

    try:
        r = subprocess.run(
            ["tmux", "has-session", "-t", f"={session}"],
            capture_output=True,
            timeout=5,
        )
        return r.returncode == 0
    except (subprocess.SubprocessError, FileNotFoundError):
        return False


# ── Top-level resolve ──────────────────────────────────────────────────────
def resolve(
    worker_name: str,
    state_dir: Path,
    *,
    session_running_fn=_tmux_session_running,
    capture_fn=capture_pane,
    now: float | None = None,
) -> dict:
    """Resolve one worker's state. Returns {state, detail, source, worker,
    target, kind}. Raises KeyError-style dict with state='unknown' target=None
    when the worker isn't registered (caller decides how to report)."""
    info = lookup_worker(state_dir, worker_name)
    if info is None:
        return {
            "worker": worker_name,
            "target": None,
            "kind": None,
            "state": "unknown",
            "detail": "unknown worker (not in registry)",
            "source": "none",
        }
    target, kind = info["target"], info["kind"]
    session = target.split(":", 1)[0]
    session_up = session_running_fn(session)
    is_dead, reason = liveness_is_dead(state_dir, worker_name, session_up, now)

    # Capture exact-match target so tmux's prefix matching can't hit a sibling
    # session ("myteam" resolving to "myteam-other").
    exact = f"={target}"
    scrape = scrape_state(exact, kind, capture_fn) if session_up else {"state": "dead", "detail": reason}
    hook = read_hook_state(state_dir, worker_name) if not is_dead else None

    merged = synthesize(hook, scrape, is_dead)
    if is_dead and not merged.get("detail"):
        merged["detail"] = reason
    return {
        "worker": worker_name,
        "target": target,
        "kind": kind,
        "state": merged["state"],
        "detail": merged.get("detail", ""),
        "source": merged["source"],
    }


# ── Observer overlay (dashboard side) ──────────────────────────────────────
def annotate_workers_with_hooks(workers: list[dict], *state_dirs: Path) -> list[dict]:
    """Overlay fresh hook state onto scanned worker dicts (agent.py → dashboard).

    Gives the dashboard event-accurate awaiting_user (with the permission
    message as detail) instead of TUI-scraped guesses. Conservative: a scrape
    meta-state (rate_limited) and a scrape-only 'typed' win — hooks can't emit
    those. Dead workers are left as-is. Workers with no hook file are untouched,
    so invited/codex/legacy panes behave exactly as before.
    """
    out: list[dict] = []
    for w in workers:
        enriched = dict(w)
        name = w.get("name", "")
        if w.get("state") == "dead" or not name:
            out.append(enriched)
            continue
        hook = None
        for sd in state_dirs:
            hook = read_hook_state(sd, name)
            if hook is not None:
                break
        if hook is None:
            out.append(enriched)
            continue
        scrape_here = {"state": _normalize_scrape_state(w.get("state", "unknown")), "detail": w.get("detail", "")}
        merged = synthesize(hook, scrape_here, is_dead=False)
        enriched["state"] = merged["state"]
        enriched["detail"] = merged.get("detail", "") or w.get("detail", "")
        enriched["state_source"] = merged["source"]
        out.append(enriched)
    return out


# ── CLI ────────────────────────────────────────────────────────────────────
def _state_dir() -> Path:
    # Mirror task_store.py's resolution so callers work whether or not STATE_DIR
    # is exported: explicit env first, else reconstruct from the team name.
    sd = os.environ.get("EE_STATE_DIR") or os.environ.get("STATE_DIR")
    if sd:
        return Path(sd)
    team = os.environ.get("DARKARCHON_TEAM", "default")
    prefix = os.environ.get("TOOL_PREFIX", "darkarchon")
    return Path.home() / f".{prefix}" / team


def _format_kv(r: dict) -> str:
    parts = [f"state={r['state']}", f"worker={r['worker']}"]
    if r.get("target"):
        parts.append(f"target={r['target']}")
    if r.get("kind"):
        parts.append(f"kind={r['kind']}")
    detail = (r.get("detail") or "").replace("\n", " ")
    if detail:
        parts.append(f"detail='{detail[:80]}'")
    parts.append(f"source={r['source']}")
    return " ".join(parts)


def main() -> int:
    p = argparse.ArgumentParser(description="Resolve a darkarchon worker's state.")
    p.add_argument("worker")
    p.add_argument("--json", action="store_true", help="emit JSON")
    p.add_argument("--field", help="print a single field's raw value (e.g. state)")
    p.add_argument("--verbose", "-v", action="store_true", help="append pane tail")
    args = p.parse_args()

    r = resolve(args.worker, _state_dir())

    if args.field:
        val = r.get(args.field, "")
        print(val if val is not None else "")
        return 0
    if args.json:
        print(json.dumps(r))
        return 0
    print(_format_kv(r))
    if args.verbose and r.get("target"):
        pane = capture_pane(f"={r['target']}", with_ansi=False)
        tail = [ln for ln in pane.splitlines() if ln.strip()][-15:]
        print("\n─── Pane (last 15 non-empty lines) ───")
        print("\n".join(tail))
    return 0


if __name__ == "__main__":
    sys.exit(main())
