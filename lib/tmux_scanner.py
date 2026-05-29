"""tmux pane discovery + state scanning for LLM CLIs.

Run on a per-host basis (called by agent.py). All subprocess calls are wrapped
so unit tests can mock them.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass

from lib.detectors.claude import classify_claude_state
from lib.detectors.codex import classify_codex_state

# Some LLM CLIs (notably Claude Code on macOS) appear in tmux's pane_current_command
# not as their CLI name but as the underlying runtime's version string, e.g. "2.1.148"
# for Node.js. Treat such panes as candidates and verify by capture content.
_NODE_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:-\S+)?$")
_CLAUDE_CANDIDATE_PROCS = {"claude", "node"}
# codex is a native binary, so its pane_current_command is just "codex".
_CODEX_CANDIDATE_PROCS = {"codex"}
# codex TUI footer/status substrings (ASCII only — composer/footer glyphs vary).
_CODEX_MARKERS = ("Working (", "Esc to interrupt", "⏎ send", "⌃J newline", "⌃T transcript")


def _looks_like_claude_candidate(process: str) -> bool:
    if process in _CLAUDE_CANDIDATE_PROCS:
        return True
    return bool(_NODE_VERSION_RE.match(process))


def _looks_like_codex_candidate(process: str) -> bool:
    return process in _CODEX_CANDIDATE_PROCS


def _has_codex_marker(plain: str) -> bool:
    hits = sum(1 for m in _CODEX_MARKERS if m in plain)
    # footer shows several markers at once; "Working (" alone is enough mid-task.
    return "Working (" in plain or "Esc to interrupt" in plain or hits >= 2


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
    allowed_processes: tuple[str, ...] = ("claude", "codex"),
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
    allowed_processes: tuple[str, ...] = ("claude", "codex"),
    window_names: tuple[str, ...] = ("claude",),
) -> list[dict]:
    """Scan all LLM panes on this host and return state-annotated worker dicts.

    Discovery + kind routing:
      1. codex process — classified via the codex detector (Working(…) busy line,
         footer for idle, auth/stream error). codex's prompt structure differs
         from Claude's, so it must NOT go through the Claude detector.
      2. Explicit — pane's tmux window_name is in `window_names`. User intent.
         Routes to the Claude or codex detector by which TUI marker is present,
         else reports 'unknown'.
      3. Implicit (claude/node) — requires a Claude TUI marker to confirm;
         otherwise dropped to avoid false positives on unrelated node panes.

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
        has_codex_marker = _has_codex_marker(plain)

        explicit = p.window_name in window_names
        claude_proc = _looks_like_claude_candidate(p.process)
        codex_proc = _looks_like_codex_candidate(p.process)

        if codex_proc:
            # Process is literally `codex` — trust it and use the codex detector.
            # (codex panes can briefly show none of the markers while booting;
            # the codex detector treats that as idle, which is safe.)
            state = classify_codex_state(plain, ansi)
            effective_process = "codex"
        elif explicit:
            # User-marked window — trust the intent, route by which TUI is present.
            if has_claude_marker:
                state = classify_claude_state(plain, ansi)
                effective_process = "claude"
            elif has_codex_marker:
                state = classify_codex_state(plain, ansi)
                effective_process = "codex"
            else:
                # Marked but no recognizable TUI — show as unknown.
                state = {"state": "unknown", "detail": f"window={p.window_name}"}
                effective_process = p.window_name
        elif claude_proc:
            # Auto-discovered by process name — require a marker to avoid false
            # positives on unrelated node panes. A codex marker here is unlikely
            # (claude/node process) but route correctly if it somehow appears.
            if has_claude_marker:
                state = classify_claude_state(plain, ansi)
                effective_process = "claude"
            elif has_codex_marker:
                state = classify_codex_state(plain, ansi)
                effective_process = "codex"
            else:
                continue  # not actually an LLM TUI — drop
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
