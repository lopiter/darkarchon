"""tmux pane discovery + state scanning for LLM CLIs.

Run on a per-host basis (called by agent.py). All subprocess calls are wrapped
so unit tests can mock them.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass

from lib.detectors.claude import classify_claude_state

# Some LLM CLIs (notably Claude Code on macOS) appear in tmux's pane_current_command
# not as their CLI name but as the underlying runtime's version string, e.g. "2.1.148"
# for Node.js. Treat such panes as candidates and verify by capture content.
_NODE_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:-\S+)?$")
_CLAUDE_CANDIDATE_PROCS = {"claude", "node"}


def _looks_like_claude_candidate(process: str) -> bool:
    if process in _CLAUDE_CANDIDATE_PROCS:
        return True
    return bool(_NODE_VERSION_RE.match(process))


@dataclass
class PaneInfo:
    pid: str
    process: str
    target: str  # session:window.pane
    cwd: str
    window_name: str


def _run_tmux(args: list[str], timeout: float = 5.0) -> tuple[int, str]:
    """Wrapper for tmux invocation. Returns (returncode, stdout)."""
    try:
        res = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return res.returncode, res.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return 1, ""


def list_llm_panes(
    allowed_processes: tuple[str, ...] = ("claude",),
    window_names: tuple[str, ...] = ("claude",),
) -> list[PaneInfo]:
    """Enumerate all tmux panes and filter to LLM candidates.

    A pane is included if its foreground process is in `allowed_processes`,
    OR its process looks like a Node version string (Claude Code on macOS),
    OR its tmux window_name is in `window_names` (explicit user marking via
    `tmux rename-window`).
    """
    rc, out = _run_tmux(
        [
            "tmux",
            "list-panes",
            "-a",
            "-F",
            "#{pane_pid} #{pane_current_command} #{session_name}:#{window_index}.#{pane_index} #{window_name} #{pane_current_path}",
        ]
    )
    if rc != 0:
        return []

    panes: list[PaneInfo] = []
    for line in out.splitlines():
        parts = line.split(None, 4)
        if len(parts) < 5:
            continue
        pid, process, target, window_name, cwd = parts
        match_proc = process in allowed_processes or bool(_NODE_VERSION_RE.match(process))
        match_window = window_name in window_names
        if not (match_proc or match_window):
            continue
        panes.append(PaneInfo(pid=pid, process=process, target=target, window_name=window_name, cwd=cwd))
    return panes


def capture_pane(target: str, with_ansi: bool = False) -> str:
    """Capture pane content. Plain by default; -e (with ANSI escapes) when with_ansi=True."""
    args = ["tmux", "capture-pane", "-p", "-t", target]
    if with_ansi:
        args.insert(2, "-e")
        args.extend(["-S", "-8"])
    else:
        args.insert(2, "-J")
    rc, out = _run_tmux(args)
    return out if rc == 0 else ""


def scan_panes(
    allowed_processes: tuple[str, ...] = ("claude",),
    window_names: tuple[str, ...] = ("claude",),
) -> list[dict]:
    """Scan all LLM panes on this host and return state-annotated worker dicts.

    Two-signal discovery:
      1. Explicit — pane's tmux window_name is in `window_names`. User intent.
         Trusts the marking; classifies via Claude detector if marker present,
         else reports as 'unknown' (could be codex/gemini/etc).
      2. Implicit — pane's process name matches `allowed_processes` or a Node
         version string. Requires Claude TUI marker to confirm; otherwise the
         pane is dropped to avoid false positives on unrelated node panes.

    Returned shape per pane:
        {
            "target": "session:window.pane",
            "process": "claude",
            "window_name": "claude",
            "cwd": "/path",
            "pane_pid": "1234",
            "state": "idle|busy|typed|compacting|rate_limited|unknown",
            "detail": "...",
            "recent_output": ["line 1", "line 2", ...],  # last 8 non-empty lines
        }
    """
    panes = list_llm_panes(allowed_processes, window_names)
    out: list[dict] = []
    for p in panes:
        plain = capture_pane(p.target, with_ansi=False)
        ansi = capture_pane(p.target, with_ansi=True)
        has_claude_marker = "❯" in plain or "─" in plain

        explicit = p.window_name in window_names
        implicit = _looks_like_claude_candidate(p.process)

        if explicit:
            # User-marked window — trust the intent.
            if has_claude_marker:
                state = classify_claude_state(plain, ansi)
                effective_process = "claude"
            else:
                # Marked but no Claude TUI (could be codex/gemini/etc) — show as unknown.
                state = {"state": "unknown", "detail": f"window={p.window_name}"}
                effective_process = p.window_name
        elif implicit:
            # Auto-discovered by process name — require marker to avoid false positives.
            if not has_claude_marker:
                continue  # not actually Claude — drop
            state = classify_claude_state(plain, ansi)
            effective_process = "claude"
        else:
            # Shouldn't happen given the filter, but be defensive.
            continue

        # Trim the captured pane to the last 8 non-empty lines for the
        # detail panel's Recent Output. capture_pane was already invoked
        # above for state classification, so no extra subprocess call.
        recent_output = [ln for ln in plain.rstrip().split("\n") if ln.strip()][-8:]

        out.append(
            {
                "target": p.target,
                "process": effective_process,
                "window_name": p.window_name,
                "cwd": p.cwd,
                "pane_pid": p.pid,
                "state": state["state"],
                "detail": state["detail"],
                "recent_output": recent_output,
            }
        )
    return out
