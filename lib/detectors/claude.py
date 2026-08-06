"""Claude Code TUI state detector — pure function over captured pane text.

Extracted from `dashboard.py`'s `get_pane_state` so the same logic can be unit-
tested with sample captures and reused by the multi-host agent without invoking
tmux directly.
"""

import re

ANSI = re.compile(r"\x1b\[[0-9;]*m")
# Active spinner uses varying glyphs (✽ ✻ ★ · ⠁ etc.) — match the gerund.
# Claude Code TUI localizes the verb based on user language: English shows
# e.g. "Whisking…"; other locales localize the verb but keep the trailing "…".
# Korean, for example, ends its progress line with the Hangul marker matched
# below. We accept either ending so the detector doesn't false-idle on
# non-English workers.
# `still running` used to be matched here too, but it false-positived on
# Claude's "* Cooked for 27s · 2 shells still running" status line, which
# means the response is *done* and only background shells are alive
# (Claude itself is accepting prompts — idle). It is now reported as a
# separate `shells_running` flag instead of busy: the resolver uses it to
# stop a live hook=busy from being self-healed to idle (a foreground wait
# on a shell can render exactly this frame mid-turn), while a hook=idle
# worker with a long-lived background shell (dev server) still counts as
# idle and stays dispatchable.
BUSY_PATTERN = re.compile(r"[A-Za-z]+ing…|중\s*…")  # "중 …" = Korean "…-ing" progress marker (functional — keep)
# Live status suffix "· N shell(s) still running". Only meaningful in the
# activity area right above the prompt separator — the same text lingers in
# scrollback long after the shells exit, so it must never be searched
# screen-wide (verified on live captures: every frame of a session matched).
SHELLS_RUNNING_PATTERN = re.compile(r"\d+\s+shells?\s+still running")
# Compacting matches BUSY too — check first so it doesn't get classified as busy.
COMPACT_PATTERN = re.compile(r"[Cc]ompacting…|compact.*in progress|/compact\s")
RATE_LIMIT_PATTERN = re.compile(
    r"rate[- ]?limit|limit reached|5h:\s*100%|wk:\s*100%|Approaching usage limit",
    re.IGNORECASE,
)
PROMPT_CHAR = "❯"
DIM_ESCAPE = re.compile(r"\x1b\[(2|0;2)m")


def strip_ansi(text: str) -> str:
    return ANSI.sub("", text)


def classify_claude_state(capture_plain: str, capture_with_ansi: str) -> dict:
    lines = capture_plain.splitlines()

    # Meta-state: rate limit. Check last 6 non-blank lines (status bar area).
    nonblank_tail = [ln for ln in lines if ln.strip()][-6:]
    tail_text = "\n".join(nonblank_tail)
    m_rate = RATE_LIMIT_PATTERN.search(tail_text)
    if m_rate:
        return {"state": "rate_limited", "detail": m_rate.group()[:60]}

    # Find prompt line (most recent ❯) and separator just above it.
    prompt_idx = None
    for i in range(len(lines) - 1, -1, -1):
        if PROMPT_CHAR in lines[i]:
            prompt_idx = i
            break

    sep_idx = None
    if prompt_idx is not None:
        for i in range(prompt_idx - 1, -1, -1):
            stripped = lines[i].strip()
            if stripped and set(stripped) <= {"─", "-", " "} and "─" in stripped:
                sep_idx = i
                break

    shells_running = False
    if sep_idx is not None and sep_idx >= 1:
        activity = [lines[i] for i in range(max(0, sep_idx - 5), sep_idx) if lines[i].strip()]
        recent = "\n".join(activity[-2:])
        if COMPACT_PATTERN.search(recent):
            return {"state": "compacting", "detail": "Compacting context"}
        m = BUSY_PATTERN.search(recent)
        if m:
            return {"state": "busy", "detail": m.group()}
        shells_running = bool(SHELLS_RUNNING_PATTERN.search(recent))
    else:
        # No clear prompt structure — fall back to checking last non-blank lines
        nonblank = [ln for ln in lines if ln.strip()]
        tail = "\n".join(nonblank[-3:])
        m = BUSY_PATTERN.search(tail)
        if m:
            return {"state": "busy", "detail": m.group()}
        shells_running = bool(SHELLS_RUNNING_PATTERN.search(tail))

    # Typed-but-unsent? Use ANSI capture to distinguish placeholder (dim) vs user input.
    for line in reversed(capture_with_ansi.splitlines()):
        if PROMPT_CHAR in line:
            after = line.split(PROMPT_CHAR, 1)[1].lstrip()
            plain_after = strip_ansi(after).strip()
            if plain_after:
                if not DIM_ESCAPE.search(after):
                    detail = plain_after[:80] + ("…" if len(plain_after) > 80 else "")
                    return {"state": "typed", "detail": detail, "shells_running": shells_running}
            break

    return {
        "state": "idle",
        "detail": "shell still running" if shells_running else "",
        "shells_running": shells_running,
    }
