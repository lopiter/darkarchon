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
# codex TUI substrings. Spans versions: 0.34 showed a footer
# ("⏎ send  ⌃J newline  ⌃T transcript"); 0.135 dropped the footer and shows an
# "OpenAI Codex (vX)" banner + "Working (Ns • esc to interrupt)" while busy.
_CODEX_MARKERS = (
    "OpenAI Codex",
    "Working (",
    "esc to interrupt",
    "⏎ send",
    "⌃J newline",
    "⌃T transcript",
)


def _looks_like_claude_candidate(process: str) -> bool:
    if process in _CLAUDE_CANDIDATE_PROCS:
        return True
    return bool(_NODE_VERSION_RE.match(process))


def _looks_like_codex_candidate(process: str) -> bool:
    return process in _CODEX_CANDIDATE_PROCS


def _has_codex_marker(plain: str) -> bool:
    lower = plain.lower()
    # Strong standalone signals (busy line / banner) — present in any one suffices.
    if "working (" in lower or "esc to interrupt" in lower or "openai codex" in lower:
        return True
    # Otherwise require ≥2 of the (older) footer hints to avoid false positives.
    return sum(1 for m in _CODEX_MARKERS if m in plain) >= 2


def _pane_window_keys(target: str, window_name: str, window_id: str = "") -> list[str]:
    """Registry keys a pane could match. The registry stores `session:window-name`
    (spawn-worker builds the target from the worker NAME), while a scanned pane's
    target is `session:window-index.pane-index`. Try both shapes so a registered
    worker is recognized regardless of how the pane is addressed.

    The tmux window id (`@5`) goes first when known: it is assigned at creation
    and never changes, whereas both other shapes are mutable — a window can be
    renamed, and window indices shift as windows are closed."""
    keys = [window_id] if window_id else []
    keys.append(target)
    if "." in target.split(":", 1)[-1]:
        keys.append(target.rsplit(".", 1)[0])  # strip .pane
    if window_name:
        session = target.split(":", 1)[0]
        keys.append(f"{session}:{window_name}")
    return keys


@dataclass
class PaneInfo:
    pid: str
    process: str
    target: str  # session:window.pane
    cwd: str
    window_name: str
    # tmux's immutable handle for the window ('@5'). Unique per tmux server and
    # stable for the window's whole life, unlike name or index.
    window_id: str = ""
    # True when an attached client is currently viewing this pane (session
    # attached + window active in its session + pane active in its window).
    # Used to suppress desktop notifications for the pane the user is looking
    # at — finishing a turn you're watching needs no alert.
    focused: bool = False


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
    known_kinds: dict[str, str] | None = None,
) -> list[PaneInfo]:
    """Enumerate all tmux panes and filter to LLM candidates.

    A pane is included if its foreground process is in `allowed_processes`,
    OR its process looks like a Node version string (Claude Code on macOS),
    OR its tmux window_name is in `window_names` (explicit user marking via
    `tmux rename-window`),
    OR it matches a registered worker in `known_kinds` (so a codex worker whose
    process shows as plain `node` — e.g. an nvm-installed codex — is still found).
    """
    known_kinds = known_kinds or {}
    rc, out = _run_tmux(
        [
            "tmux",
            "list-panes",
            "-a",
            "-F",
            # session_attached/window_active/pane_active/window_id come right
            # after pane_pid (all single tokens) so pane_current_path stays the
            # trailing remainder and can still contain spaces.
            "#{pane_pid} #{session_attached} #{window_active} #{pane_active} #{window_id} #{pane_current_command} #{session_name}:#{window_index}.#{pane_index} #{window_name} #{pane_current_path}",
        ]
    )
    if rc != 0:
        return []

    panes: list[PaneInfo] = []
    for line in out.splitlines():
        parts = line.split(None, 8)
        if len(parts) < 9:
            continue
        (pid, attached, win_active, pane_active, window_id,
         process, target, window_name, cwd) = parts
        match_proc = process in allowed_processes or bool(_NODE_VERSION_RE.match(process))
        match_window = window_name in window_names
        match_known = any(
            k in known_kinds for k in _pane_window_keys(target, window_name, window_id)
        )
        if not (match_proc or match_window or match_known):
            continue
        focused = attached.isdigit() and int(attached) > 0 and win_active == "1" and pane_active == "1"
        panes.append(
            PaneInfo(
                pid=pid,
                process=process,
                target=target,
                window_name=window_name,
                window_id=window_id,
                cwd=cwd,
                focused=focused,
            )
        )
    return panes


def capture_pane_title(target: str) -> str:
    """Read the pane's OSC title (#{pane_title}) — one cheap tmux call.

    Agent CLIs publish state here (live-verified 2026-08-06): codex prefixes a
    braille spinner while working and sets "[ ! ] Action Required | <dir>" when
    blocked on an approval; gemini toggles "◇  Ready (<dir>)" ↔
    "✦  Working… (<dir>)". Claude Code's title does NOT toggle with state
    (verified over 250+ sampled frames), so it is not a claude signal.
    """
    rc, out = _run_tmux(["tmux", "display-message", "-p", "-t", target, "#{pane_title}"])
    return out.strip() if rc == 0 else ""


def capture_pane_process(target: str) -> str:
    """Read the pane's foreground command (#{pane_current_command}).

    Used to tell an empty shell apart from an agent still running in the pane —
    a distinction pane CONTENT cannot make, since a dead worker's leftover shell
    and a live agent at an empty prompt both scrape as idle-looking frames.
    """
    rc, out = _run_tmux(
        ["tmux", "display-message", "-p", "-t", target, "#{pane_current_command}"]
    )
    return out.strip() if rc == 0 else ""


def looks_like_agent_process(process: str) -> bool:
    """True when this foreground command could be an agent CLI rather than a shell.

    Deliberately the same loose test the pane scanner uses to pick candidates:
    claude appears as node's version string on macOS, so anything node-shaped
    counts. Over-reporting here is the safe direction — the one caller uses it
    to warn "something is still running in this pane, don't kill it".
    """
    return _looks_like_claude_candidate(process) or _looks_like_codex_candidate(process)


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
    known_kinds: dict[str, str] | None = None,
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
    known_kinds = known_kinds or {}
    panes = list_llm_panes(allowed_processes, window_names, known_kinds)
    out: list[dict] = []
    for p in panes:
        plain = capture_pane(p.target, with_ansi=False)
        ansi = capture_pane(p.target, with_ansi=True)
        has_claude_marker = "❯" in plain or "─" in plain
        has_codex_marker = _has_codex_marker(plain)

        # Registry is authoritative: if this pane is a registered worker, route
        # classification by its recorded kind rather than guessing from process
        # name or TUI glyphs. This is the robust fix for (a) codex showing as a
        # plain `node` process and (b) codex's box-drawing `─` falsely matching
        # the Claude marker — both would otherwise misroute to the wrong detector.
        registered_kind = None
        for k in _pane_window_keys(p.target, p.window_name, p.window_id):
            if k in known_kinds:
                registered_kind = known_kinds[k]
                break

        explicit = p.window_name in window_names
        claude_proc = _looks_like_claude_candidate(p.process)
        codex_proc = _looks_like_codex_candidate(p.process)

        if registered_kind == "codex":
            state = classify_codex_state(plain, ansi, capture_pane_title(p.target))
            effective_process = "codex"
        elif registered_kind == "claude":
            state = classify_claude_state(plain, ansi)
            effective_process = "claude"
        elif codex_proc:
            # Process is literally `codex` — trust it and use the codex detector.
            # (codex panes can briefly show none of the markers while booting;
            # the codex detector treats that as idle, which is safe.)
            state = classify_codex_state(plain, ansi, capture_pane_title(p.target))
            effective_process = "codex"
        elif explicit:
            # User-marked window — trust the intent, route by which TUI is present.
            # Check codex first: its box-drawing `─` also satisfies has_claude_marker,
            # so a claude-first check would misroute codex panes.
            if has_codex_marker:
                state = classify_codex_state(plain, ansi, capture_pane_title(p.target))
                effective_process = "codex"
            elif has_claude_marker:
                state = classify_claude_state(plain, ansi)
                effective_process = "claude"
            else:
                # Marked but no recognizable TUI — show as unknown.
                state = {"state": "unknown", "detail": f"window={p.window_name}"}
                effective_process = p.window_name
        elif claude_proc:
            # Auto-discovered by process name — require a marker to avoid false
            # positives on unrelated node panes. Codex-first for the same `─` reason.
            if has_codex_marker:
                state = classify_codex_state(plain, ansi, capture_pane_title(p.target))
                effective_process = "codex"
            elif has_claude_marker:
                state = classify_claude_state(plain, ansi)
                effective_process = "claude"
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
                "window_id": p.window_id,
                "cwd": p.cwd,
                "pane_pid": p.pid,
                "focused": p.focused,
                "state": state["state"],
                "detail": state["detail"],
                "shells_running": bool(state.get("shells_running")),
                "recent_output": recent_output,
            }
        )
    return out
