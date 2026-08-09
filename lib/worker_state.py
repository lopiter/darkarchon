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
from lib.detectors.gemini import classify_gemini_state  # noqa: E402
from lib.heartbeat import (  # noqa: E402
    HEARTBEAT_STALE_SEC,
    heartbeat_age_sec,
    is_pid_alive,
    read_heartbeat,
)
from lib.tmux_scanner import (  # noqa: E402
    capture_pane,
    capture_pane_process,
    capture_pane_title,
    looks_like_agent_process,
)
from lib.worker_resolver import parse_registry_file, read_scoped  # noqa: E402

# Canonical state vocabulary emitted by this resolver.
#   dead                — worker gone (no session / heartbeat stale / pid dead /
#                         session ended). May carry orphaned=True when the
#                         window still hosts an unregistered agent.
#   rate_limited        — API limit reached (scrape meta-state)
#   error               — codex auth/stream failure (scrape meta-state)
#   awaiting_permission — blocked on a tool-permission prompt (PermissionRequest hook)
#   awaiting_user       — blocked on an explicit question (Notification hook)
#   compacting          — /compact in progress
#   busy                — actively processing a turn
#   unsent              — user has typed but unsent input on the prompt line (scrape only)
#   idle                — at an empty prompt, ready for dispatch
#   unknown             — no recognizable signal
_SCRAPE_META = ("rate_limited", "error")

# Hook states meaning the session is over. Reported as `dead`: the pane may still
# exist (a worker that exited leaves a shell behind), and a bare shell scrapes as
# `idle`, so without this an ended worker would look ready and swallow dispatches.
# Scrape must not override these — a fresh SessionStart is what revives a worker.
_HOOK_TERMINAL = ("ended",)


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


def scrape_state(target: str, kind: str, capture_fn=capture_pane, title_fn=capture_pane_title) -> dict:
    """Capture the pane and classify via the kind-appropriate detector.

    codex and gemini also read the pane's OSC title (#{pane_title}) — both set
    state there (codex: braille spinner / "Action Required"; gemini:
    "✦ Working…" / "◇ Ready"; live-verified 2026-08). Claude Code's title does
    not toggle with state, so the claude detector ignores it.
    """
    plain = capture_fn(target, with_ansi=False)
    ansi = capture_fn(target, with_ansi=True)
    if not plain and not ansi:
        return {"state": "unknown", "detail": "no pane capture"}
    if kind == "codex":
        st = classify_codex_state(plain, ansi, title_fn(target))
    elif kind == "gemini":
        st = classify_gemini_state(plain, ansi, title_fn(target))
    else:
        st = classify_claude_state(plain, ansi)
    out = {"state": _normalize_scrape_state(st["state"]), "detail": st.get("detail", "")}
    if st.get("shells_running"):
        out["shells_running"] = True
    return out


# ── Pure merge policy ──────────────────────────────────────────────────────
def synthesize(hook: dict | None, scrape: dict, is_dead: bool) -> dict:
    """Combine the three layers into one {state, detail, source}. Pure.

    Precedence:
      - is_dead wins over everything.
      - scrape meta-states (rate_limited, error) always honored: hooks can't
        emit them and they mean the worker can't progress.
      - no hook (invited/codex/legacy) → scrape verbatim.
      - hook says the session ended → dead, and scrape cannot argue: the pane it
        reads is a bare shell, which classifies as idle.
      - hook says busy but scrape sees idle/unsent → trust scrape. A missed Stop
        hook would otherwise pin the worker busy forever; scrape self-heals it.
        EXCEPT when the scrape carries shells_running: a foreground wait on a
        shell renders an idle-looking frame ("✻ Cogitated · 1 shell still
        running" over an empty prompt) mid-turn, so a live busy hook wins there.
        A worker whose turn really ended (Stop → hook idle) with a long-lived
        background shell keeps counting as idle — this guard is busy-hook-only.
      - scrape sees an open modal dialog (awaiting_permission) → it wins over
        any live hook state. The PermissionRequest hook does not reach every
        build or settings merge, so a blocked worker can carry a stale idle or
        busy hook; the screen is unambiguous there (the dialog replaces the
        input prompt) and idle would advertise it as dispatchable.
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
    if h in _HOOK_TERMINAL:
        return {"state": "dead", "detail": hook.get("detail", "") or "session ended",
                "source": "hook"}
    if s == "awaiting_permission":
        return {**scrape, "source": "scrape-overlay"}
    if h == "busy" and s in ("idle", "unsent"):
        if scrape.get("shells_running"):
            return {
                "state": "busy",
                "detail": hook.get("detail", "") or "foreground shell still running",
                "source": "hook(shells-running)",
            }
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


# ── Orphaned pane detection ────────────────────────────────────────────────
def detect_orphan_process(target: str, process_fn=capture_pane_process) -> str:
    """The agent process still running in a dead worker's pane, or ''.

    A worker reported dead has, from darkarchon's side, exactly one meaning: no
    heartbeat and/or a SessionEnd — nobody is answering for that name. It says
    nothing about the WINDOW, which outlives the worker process. When someone
    kills a worker's claude (context full, a stuck turn) and starts their own
    claude in that same window, the registry still points at a pane that now
    hosts a live, unregistered agent.

    That combination is the dangerous one: the honest cleanup for a dead worker
    is kill-worker.sh, which destroys the window — and with it the conversation
    the human has been building there. Surfacing the process lets the cleanup
    paths refuse, and lets the dashboard say "occupied" instead of just "dead".
    """
    proc = process_fn(target)
    return proc if proc and looks_like_agent_process(proc) else ""


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
    title_fn=capture_pane_title,
    process_fn=capture_pane_process,
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
    scrape = scrape_state(exact, kind, capture_fn, title_fn) if session_up else {"state": "dead", "detail": reason}
    hook = read_hook_state(state_dir, worker_name) if not is_dead else None

    merged = synthesize(hook, scrape, is_dead)
    if is_dead and not merged.get("detail"):
        merged["detail"] = reason

    result = {
        "worker": worker_name,
        "target": target,
        "kind": kind,
        "state": merged["state"],
        "detail": merged.get("detail", ""),
        "source": merged["source"],
    }

    # A dead worker whose window still hosts an agent: the state stays `dead`
    # (nobody answers for this name, so it must not take dispatches), but the
    # pane is NOT free for the cleanup paths to destroy. Reported as a separate
    # flag rather than a new state so every existing `case $state in` — which
    # would otherwise fall through to its "unrecognized, proceed anyway" arm —
    # keeps behaving exactly as before.
    if merged["state"] == "dead" and session_up:
        proc = detect_orphan_process(exact, process_fn)
        if proc:
            result["orphaned"] = True
            result["orphan_process"] = proc
            note = f"pane still runs an unregistered agent ({proc})"
            result["detail"] = f"{result['detail']}; {note}" if result["detail"] else note

    return result


# ── Wait-until primitive ───────────────────────────────────────────────────
def wait_until(
    worker_name: str,
    state_dir: Path,
    until: set[str],
    *,
    timeout: float = 600.0,
    interval: float = 2.0,
    resolve_fn=None,
    sleep_fn=None,
    monotonic_fn=None,
) -> tuple[dict, str]:
    """Poll resolve() until the worker reaches one of `until` states.

    The orchestrator-side wait primitive (herdr's `agent wait --until STATUS`):
    dispatch, then block on "idle or needs-a-human" instead of scraping panes
    in a shell loop. Returns (last_resolved, outcome) where outcome is:
      reached  — state ∈ until
      timeout  — deadline hit; last_resolved holds the final observation
      dead     — worker died and 'dead' was not an accepted state (early exit:
                 nothing arrives on a dead worker except a respawn, which makes
                 a fresh wait)
      unknown  — worker not in the registry (early exit, waiting can't fix it)
    """
    import time as _time

    resolve_fn = resolve_fn or (lambda w: resolve(w, state_dir))
    sleep_fn = sleep_fn or _time.sleep
    monotonic_fn = monotonic_fn or _time.monotonic

    deadline = monotonic_fn() + timeout
    while True:
        r = resolve_fn(worker_name)
        if r.get("target") is None:
            return r, "unknown"
        if r["state"] in until:
            return r, "reached"
        if r["state"] == "dead" and "dead" not in until:
            return r, "dead"
        if monotonic_fn() >= deadline:
            return r, "timeout"
        sleep_fn(interval)


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
        hook = read_scoped(read_hook_state, w, name, state_dirs)
        if hook is None:
            out.append(enriched)
            continue
        scrape_here = {"state": _normalize_scrape_state(w.get("state", "unknown")), "detail": w.get("detail", "")}
        if w.get("shells_running"):
            scrape_here["shells_running"] = True
        merged = synthesize(hook, scrape_here, is_dead=False)
        enriched["state"] = merged["state"]
        enriched["detail"] = merged.get("detail", "") or w.get("detail", "")
        enriched["state_source"] = merged["source"]
        out.append(enriched)
    return out


# ── CLI ────────────────────────────────────────────────────────────────────
def _state_dir() -> Path:
    # Mirror task_store.py's resolution so callers work whether or not STATE_DIR
    # is exported. STATE_DIR (exported by _lib.sh for the team currently being
    # operated on) wins over EE_STATE_DIR (this process's own worker identity):
    # a spawned orchestrator managing its own team must resolve ITS workers'
    # states, not report against the state dir of the team that spawned it.
    sd = os.environ.get("STATE_DIR") or os.environ.get("EE_STATE_DIR")
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
    if r.get("orphaned"):
        parts.append("orphaned=1")
    return " ".join(parts)


def main() -> int:
    p = argparse.ArgumentParser(description="Resolve a darkarchon worker's state.")
    p.add_argument("worker")
    p.add_argument("--json", action="store_true", help="emit JSON")
    p.add_argument("--field", help="print a single field's raw value (e.g. state)")
    p.add_argument("--verbose", "-v", action="store_true", help="append pane tail")
    p.add_argument("--until", help="comma-separated states: poll until one is reached")
    p.add_argument("--timeout", type=float, default=600.0, help="--until deadline in seconds (default 600)")
    p.add_argument("--interval", type=float, default=2.0, help="--until poll interval in seconds (default 2)")
    args = p.parse_args()

    if args.until:
        until = {s.strip() for s in args.until.split(",") if s.strip()}
        r, outcome = wait_until(
            args.worker, _state_dir(), until,
            timeout=args.timeout, interval=args.interval,
        )
        if args.json:
            print(json.dumps({**r, "outcome": outcome}))
        elif args.field:
            print(r.get(args.field, "") or "")
        else:
            print(f"outcome={outcome} {_format_kv(r)}")
        return {"reached": 0, "unknown": 1, "timeout": 2, "dead": 3}[outcome]

    r = resolve(args.worker, _state_dir())

    if args.field:
        val = r.get(args.field, "")
        # Shell callers test these with [ "$x" = "1" ]; Python's True/False
        # repr would make every falsy flag a non-empty string.
        if isinstance(val, bool):
            val = "1" if val else ""
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
