"""Rewrite orchestrator.txt from session:index.pane to pane @window_id.

Window indices are reused after a respawn; window ids are not. Default is
dry-run. --apply writes or deletes.

A tmux target like `=session:bogusname` silently falls back to the session's
active window — never look up by name. Markers are `session:index.pane`; we
query that index and require the result to be that same index.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from dashboard import _parse_orch_marker  # noqa: E402
from lib.tmux_scanner import looks_like_agent_process  # noqa: E402
from lib.worker_resolver import parse_registry_file  # noqa: E402


def parse_pane_key(pane: str) -> tuple[str, str, str] | None:
    """`3hour:1.1` → (session, window_index, pane_index). None if not index form."""
    pane = (pane or "").strip()
    if ":" not in pane or "." not in pane.split(":", 1)[-1]:
        return None
    session, rest = pane.split(":", 1)
    win, _, pidx = rest.partition(".")
    if not session or not win.isdigit() or not pidx.isdigit():
        return None
    return session, win, pidx


def classify_tmux_lookup(
    stdout: str, rc: int, session: str, win: str, pane: str
) -> dict:
    """Interpret `tmux display-message` for an index target.

    A missing session still exits 0 with blank fields — that is dead, not
    ambiguous. Pipe-separated so pane_current_command may contain spaces.
    """
    if rc != 0:
        return {"status": "dead"}
    parts = (stdout or "").rstrip("\n").split("|")
    while len(parts) < 6:
        parts.append("")
    sess, widx, pidx, wid, wname, cmd = (p.strip() for p in parts[:6])
    if not sess or not widx or not pidx or not wid:
        return {"status": "dead"}
    try:
        same = (
            sess == session
            and int(widx) == int(win)
            and int(pidx) == int(pane)
        )
    except ValueError:
        same = False
    if not same:
        return {
            "status": "ambiguous",
            "detail": (
                f"tmux returned {sess}:{widx}.{pidx} for "
                f"{session}:{win}.{pane} (active-window fallback?)"
            ),
        }
    if not wid.startswith("@"):
        return {"status": "ambiguous", "detail": f"bad window_id {wid!r}"}
    return {
        "status": "live",
        "pane": f"{sess}:{int(widx)}.{int(pidx)}",
        "window_id": wid,
        "window_name": wname,
        "process": cmd,
    }


def tmux_resolve_index(session: str, win: str, pane: str) -> dict:
    """Look up session:win.pane by index. Never by window name."""
    target = f"={session}:{win}.{pane}"
    try:
        proc = subprocess.run(
            [
                "tmux", "display-message", "-p", "-t", target,
                "#{session_name}|#{window_index}|#{pane_index}|"
                "#{window_id}|#{window_name}|#{pane_current_command}",
            ],
            capture_output=True, text=True, timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "ambiguous", "detail": f"tmux error: {exc}"}
    return classify_tmux_lookup(proc.stdout, proc.returncode, session, win, pane)


def registry_staff(state_dir: Path, hit: dict) -> bool:
    """True if the live pane is a spawned staff worker of this team.

    Same rule as dashboard._marker_is_staff: registered, not EXTERNAL,
    role set and not orchestrator. Matches by WINDOW_ID or session:window_name.
    """
    entries = parse_registry_file(state_dir / "workers-runtime.env")
    wid = hit.get("window_id") or ""
    wname = hit.get("window_name") or ""
    pane = hit.get("pane") or ""
    session = pane.split(":", 1)[0] if ":" in pane else ""
    name_target = f"{session}:{wname}" if session and wname else ""
    seen: set[str] = set()
    for meta in entries.values():
        t = meta.get("target") or ""
        if not t or t in seen:
            continue
        seen.add(t)
        if (wid and meta.get("window_id") == wid) or (name_target and t == name_target):
            if meta.get("external"):
                return False
            role = (meta.get("role") or "").strip()
            return bool(role) and role != "orchestrator"
    return False


def tmux_window_id_live(window_id: str) -> bool:
    """True if some pane currently has this window id (exact, via list-panes)."""
    if not window_id.startswith("@"):
        return False
    try:
        proc = subprocess.run(
            ["tmux", "list-panes", "-a", "-F", "#{window_id}"],
            capture_output=True, text=True, timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if proc.returncode != 0:
        return False
    return window_id in proc.stdout.split()


def plan_one(
    path: Path,
    *,
    resolve_index=tmux_resolve_index,
    id_live=tmux_window_id_live,
    is_agent=looks_like_agent_process,
    is_staff=None,
) -> dict:
    """Decide rewrite / delete / skip / ok for one orchestrator.txt."""
    team = path.parent.name
    if is_staff is None:
        is_staff = lambda hit, p=path: registry_staff(p.parent, hit)
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"action": "skip", "path": str(path), "team": team,
                "detail": f"unreadable: {exc}"}
    line = next((ln for ln in raw.splitlines() if ln.strip()), "")
    pane, wid = _parse_orch_marker(line)
    if not pane and not wid:
        return {"action": "delete", "path": str(path), "team": team,
                "detail": "empty marker", "old": line}

    if wid:
        if id_live(wid):
            return {"action": "ok", "path": str(path), "team": team,
                    "detail": f"already window-id {wid}", "old": line}
        return {"action": "delete", "path": str(path), "team": team,
                "detail": f"window_id {wid} is gone", "old": line}

    parsed = parse_pane_key(pane)
    if parsed is None:
        return {"action": "skip", "path": str(path), "team": team,
                "detail": f"not session:index.pane ({pane!r})", "old": line}
    session, win, pidx = parsed
    hit = resolve_index(session, win, pidx)
    st = hit.get("status")
    if st == "dead":
        return {"action": "delete", "path": str(path), "team": team,
                "detail": f"{pane} not in tmux", "old": line}
    if st == "ambiguous":
        return {"action": "skip", "path": str(path), "team": team,
                "detail": hit.get("detail") or "ambiguous tmux lookup",
                "old": line}
    process = hit.get("process") or ""
    if not is_agent(process):
        return {"action": "delete", "path": str(path), "team": team,
                "detail": f"{pane} is not an agent ({process or 'empty'})",
                "old": line}
    if is_staff(hit):
        return {"action": "delete", "path": str(path), "team": team,
                "detail": f"{pane} is registered staff of {team}",
                "old": line}
    new = f"{hit['pane']} {hit['window_id']}"
    return {"action": "rewrite", "path": str(path), "team": team,
            "detail": f"{line} -> {new}", "old": line, "new": new}


def discover_marker_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    found: list[Path] = []
    for p in sorted(root.rglob("orchestrator.txt")):
        if p.is_file():
            found.append(p)
    return found


def apply_plan(item: dict) -> None:
    path = Path(item["path"])
    if item["action"] == "delete":
        path.unlink(missing_ok=True)
    elif item["action"] == "rewrite":
        path.write_text(item["new"] + "\n")


def run(root: Path, apply: bool = False, *,
        resolve_index=tmux_resolve_index,
        id_live=tmux_window_id_live,
        is_agent=looks_like_agent_process,
        is_staff=None) -> list[dict]:
    plans = [
        plan_one(p, resolve_index=resolve_index, id_live=id_live,
                 is_agent=is_agent, is_staff=is_staff)
        for p in discover_marker_files(root)
    ]
    if apply:
        for item in plans:
            if item["action"] in ("delete", "rewrite"):
                apply_plan(item)
    return plans


def _print_report(plans: list[dict], apply: bool) -> None:
    if not plans:
        print("No orchestrator.txt files found.")
        return
    counts: dict[str, int] = {}
    for item in plans:
        counts[item["action"]] = counts.get(item["action"], 0) + 1
        tag = item["action"].upper()
        print(f"  [{tag}] {item['team']}: {item['detail']}")
    print()
    print(
        "totals: "
        + ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    )
    if not apply:
        print("dry-run: nothing changed. Re-run with --apply to write.")
    else:
        print("applied.")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Rewrite orchestrator.txt to window-id form (default: dry-run)."
    )
    p.add_argument("--root", default="",
                   help="parent of team state dirs (default: HOST_STATE_DIR)")
    p.add_argument("--apply", action="store_true",
                   help="write/delete; without this flag, report only")
    args = p.parse_args(argv)
    root = Path(args.root).expanduser() if args.root else None
    if root is None:
        print("ERROR: --root is required (shell wrapper passes HOST_STATE_DIR)",
              file=sys.stderr)
        return 1
    plans = run(root, apply=args.apply)
    _print_report(plans, args.apply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
