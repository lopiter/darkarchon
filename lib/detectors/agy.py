"""Antigravity CLI (Google `agy`) TUI state detector — pure function over
captured pane text.

Counterpart to detectors/{claude,codex,gemini,grok}.py for agy workers. agy is
the successor of Gemini CLI but shares nothing with its TUI. Rules ported from
herdr's antigravity manifest (evidence ~2026-06) and re-verified live against
agy 1.2.2 in a tmux pane (2026-09-12):

  - agy publishes NO OSC title (the pane title stays whatever the shell set,
    e.g. the hostname), so — unlike gemini/grok/codex — everything comes from
    the screen. The footer line is the anchor: idle reads
    "? for shortcuts   <model> · <effort>", a running turn reads
    "esc to cancel   …", and a turn with a queued follow-up reads
    "Press up to edit queued messages   …".
  - Working turns also draw a live status line with a braille spinner and an
    "-ing" word ("⡿  Generating...", "⣽  Running command..."), and a
    background-task chip ("· 2 tasks") while tasks run after the turn.
  - Permission dialogs render "Requesting permission for:" followed by the
    question agy 1.2 words per tool ("Run this command?", "Allow access to this
    URL?", "Allow calling this tool?"; older builds: "Do you want to proceed?")
    and a "↑/↓ Navigate · tab Amend · ctrl+g edit/expand command" hint. The
    footer still says "esc to cancel" there, so the dialog is checked first.
  - The startup trust dialog reads "Do you trust the contents of this project?"
    with "> Yes, I trust this folder / No, exit".
  - Typing into a busy pane does not interrupt the turn: the text is queued
    ("▸ <text>" above the composer) and runs as the next prompt.

States returned: "awaiting_permission" | "busy" | "idle" | "unsent" | "unknown".
"""

import re

ANSI = re.compile(r"\x1b\[[0-9;]*m")

BRAILLE = "⠀-⣿"

# ── screen: blocked ────────────────────────────────────────────────────────
TRUST_HINT = "do you trust the contents of this project?"
PERMISSION_REQUEST_HINT = "requesting permission for:"
PERMISSION_QUESTION_HINTS = (
    "run this command?",
    "allow access to this url?",
    "allow calling this tool?",
    "do you want to proceed?",
)
# 1.2.2 footer: "↑/↓ Navigate · tab Amend · ctrl+g edit/expand command";
# herdr's evidence (older): "tab amend" + "edit command".
PERMISSION_CONTROL_HINTS = ("tab amend", "edit command", "edit/expand command")

# ── screen: working ────────────────────────────────────────────────────────
SPINNER_PATTERN = re.compile(f"^\\s*[{BRAILLE}]+\\s+[A-Za-z]+ing\\b", re.MULTILINE)
BUSY_FOOTER_HINT = "esc to cancel"
QUEUED_FOOTER_HINT = "queued message"
BACKGROUND_TASKS_PATTERN = re.compile(r"·\s*[1-9][0-9]*\s+task", re.IGNORECASE)

# ── screen: idle ───────────────────────────────────────────────────────────
IDLE_FOOTER_HINT = "? for shortcuts"
# The composer sits two lines above the footer, between two horizontal rules.
COMPOSER_UNSENT_PATTERN = re.compile(r"^\s*>\s*\S")


def strip_ansi(text: str) -> str:
    return ANSI.sub("", text)


def classify_agy_state(capture_plain: str, capture_with_ansi: str = "", pane_title: str = "") -> dict:
    plain = strip_ansi(capture_plain) if capture_plain else ""
    nonblank = [ln for ln in plain.splitlines() if ln.strip()]
    if not nonblank:
        return {"state": "unknown", "detail": "no pane capture"}
    # agy runs full-screen; dialogs, the status line, the composer and the
    # footer all live in the bottom slice of the visible screen.
    tail_lines = nonblank[-24:]
    tail = "\n".join(tail_lines)
    tail_lower = tail.lower()
    footer_lower = nonblank[-1].lower()

    # 1. Blocking dialogs — screen only, and before the footer: a permission
    #    dialog keeps "esc to cancel" in the footer.
    if TRUST_HINT in tail_lower:
        return {"state": "awaiting_permission", "detail": "trust folder prompt"}
    if PERMISSION_REQUEST_HINT in tail_lower and (
        any(h in tail_lower for h in PERMISSION_QUESTION_HINTS)
        or any(h in tail_lower for h in PERMISSION_CONTROL_HINTS)
    ):
        return {"state": "awaiting_permission", "detail": "permission prompt"}

    # 2. Working — footer first (cheap and stable), then the spinner line.
    if BUSY_FOOTER_HINT in footer_lower:
        m = SPINNER_PATTERN.search(tail)
        return {"state": "busy", "detail": m.group().strip()[:60] if m else "esc to cancel"}
    if QUEUED_FOOTER_HINT in footer_lower:
        return {"state": "busy", "detail": "queued message"}
    m = SPINNER_PATTERN.search(tail)
    if m:
        return {"state": "busy", "detail": m.group().strip()[:60]}
    if BACKGROUND_TASKS_PATTERN.search("\n".join(nonblank[-5:])):
        return {"state": "busy", "detail": "background tasks"}

    # 3. Idle — the footer's shortcut hint. The composer two lines above it
    #    holds any typed-but-unsent text.
    if IDLE_FOOTER_HINT in footer_lower:
        if len(nonblank) >= 3 and COMPOSER_UNSENT_PATTERN.match(nonblank[-3]):
            return {"state": "unsent", "detail": nonblank[-3].strip()[:60]}
        return {"state": "idle", "detail": ""}

    # 4. No recognizable chrome (scrolled, mid-redraw) — idle is the safe read.
    return {"state": "idle", "detail": ""}
