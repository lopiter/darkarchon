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

States returned: "error" | "busy" | "idle". (Unknown structure → "idle", which is
conservative: dispatch-safe keys off busy/error, never off idle.)
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


def strip_ansi(text: str) -> str:
    return ANSI.sub("", text)


def classify_codex_state(capture_plain: str, capture_with_ansi: str = "") -> dict:
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

    # 2. Busy — the Working(...) status line.
    m = BUSY_PATTERN.search(tail)
    if m:
        return {"state": "busy", "detail": m.group()}

    # 3. Idle — composer footer present and no busy line.
    if sum(1 for h in FOOTER_HINTS if h in tail) >= 2:
        return {"state": "idle", "detail": ""}

    # 4. Unknown structure (blank pane, codex still booting, …). Report idle so a
    #    dispatch isn't blocked forever; dispatch-safe only refuses on busy/error.
    return {"state": "idle", "detail": ""}
