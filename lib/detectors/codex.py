"""Codex CLI TUI state detector — pure function over captured pane text.

Counterpart to detectors/claude.py for OpenAI codex workers. Codex's TUI differs
from Claude's in ways that matter for busy/idle detection (verified empirically
by omc on codex-cli 0.34.0):

  - The input composer (▌) and footer ("⏎ send  ⌃J newline  ⌃T transcript …")
    stay visible even WHILE codex is working. So, unlike Claude, the presence of
    a prompt does NOT mean idle, and the footer must never be used as a busy
    signal on its own.
  - Busy is marked by a separate line: "Working (<N>s • Esc to interrupt)".
  - An expired auth token surfaces "Failed to refresh token: 401 Unauthorized"
    / "stream error" — the worker can't make progress until `codex login`.

We match ASCII substrings (not the unicode glyphs ▌ ⏎ ⌃ •) so the regexes don't
break across terminal/locale differences. Capture should be `tmux capture-pane -p`
WITHOUT scrollback (-S) because codex uses the alternate screen buffer.

The pane's OSC title is a second, cheaper signal (live-verified on codex 0.141):
codex prefixes a braille spinner to the title while working ("⠦ tmp") and sets
"[ ! ] Action Required | <dir>" while blocked on an approval prompt. The title
survives user scrolling, so it outranks screen text; screen patterns remain for
panes whose title never arrives (e.g. OSC filtered by an outer terminal).

States returned: "error" | "awaiting_permission" | "busy" | "idle". (Unknown
structure → "idle", which is conservative: dispatch-safe keys off
busy/error/awaiting, never off idle.)
"""

import re

ANSI = re.compile(r"\x1b\[[0-9;]*m")

# Auth / stream failure — codex stuck until re-login. Highest precedence.
AUTH_ERROR_PATTERN = re.compile(
    r"Failed to refresh token|401 Unauthorized|stream error",
    re.IGNORECASE,
)
# Busy: the "Working (Ns • Esc to interrupt)" status line. "Working (\d+s" is
# codex-specific; "Esc to interrupt" is also accepted (only reached on codex
# panes, which this detector is routed to by worker kind).
BUSY_PATTERN = re.compile(r"Working\s*\(\d+\s*s|Esc to interrupt", re.IGNORECASE)
# Footer substrings present whenever codex's composer is on screen (idle baseline).
FOOTER_HINTS = ("send", "newline", "transcript")

# OSC title while blocked on approval: "[ ! ] Action Required | tmp".
TITLE_BLOCKED_PATTERN = re.compile(r"Action Required", re.IGNORECASE)
# OSC title while working: braille-spinner prefix, e.g. "⠦ tmp".
TITLE_BUSY_PATTERN = re.compile(r"^[⠀-⣿]\s")
# Approval prompt on screen — codex 0.141 renders:
#   Would you like to run the following command?
#   › 1. Yes, proceed (y) … Press enter to confirm or esc to cancel
APPROVAL_PATTERN = re.compile(
    r"Would you like to run|Press enter to confirm or esc to cancel",
    re.IGNORECASE,
)


def strip_ansi(text: str) -> str:
    return ANSI.sub("", text)


def classify_codex_state(capture_plain: str, capture_with_ansi: str = "", pane_title: str = "") -> dict:
    plain = strip_ansi(capture_plain) if capture_plain else ""
    nonblank = [ln for ln in plain.splitlines() if ln.strip()]
    # Codex uses an alt-screen; the captured text is the visible screen. Restrict
    # to the bottom slice (status line + composer + footer live there) so a stale
    # scrolled line is less likely to misclassify.
    tail = "\n".join(nonblank[-12:])

    # 1. Auth / stream error preempts everything — worker can't progress.
    m = AUTH_ERROR_PATTERN.search(tail)
    if m:
        return {"state": "error", "detail": m.group()[:60]}

    # 2. Blocked on an approval prompt — needs the human, never dispatchable.
    #    Title first (survives scrolling), screen text second.
    if pane_title and TITLE_BLOCKED_PATTERN.search(pane_title):
        return {"state": "awaiting_permission", "detail": pane_title.strip()[:80]}
    m = APPROVAL_PATTERN.search(tail)
    if m:
        return {"state": "awaiting_permission", "detail": m.group()}

    # 3. Busy — spinner in the title, or the Working(...) status line.
    if pane_title and TITLE_BUSY_PATTERN.search(pane_title):
        return {"state": "busy", "detail": pane_title.strip()[:60]}
    m = BUSY_PATTERN.search(tail)
    if m:
        return {"state": "busy", "detail": m.group()}

    # 4. Idle — composer footer present and no busy line.
    if sum(1 for h in FOOTER_HINTS if h in tail) >= 2:
        return {"state": "idle", "detail": ""}

    # 5. Unknown structure (blank pane, codex still booting, …). Report idle so a
    #    dispatch isn't blocked forever; dispatch-safe only refuses on busy/error.
    return {"state": "idle", "detail": ""}
