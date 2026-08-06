"""Gemini CLI TUI state detector — pure function over captured pane text + title.

Counterpart to detectors/{claude,codex}.py for gemini-cli workers. All signals
live-verified on gemini-cli against a real tmux pane (2026-08-06):

  - The OSC pane title is the primary signal — gemini toggles it through a full
    prompt cycle: "◇  Ready (<dir>)" while idle ↔ "✦  Working… (<dir>)" while
    generating. (Unlike Claude Code, whose title does not toggle with state.)
  - Blocked dialogs keep the title at "◇ Ready", so blocked must come from
    screen text: the trust-folder dialog ("Do you trust this folder?") and tool
    approvals ("Apply this change" / "Allow execution" boxes).
  - A missing/expired API key opens a key-entry dialog whose footer reads
    "Esc to cancel, Ctrl+C to clear stored key" — the worker can't progress
    until a human intervenes, same contract as codex's auth "error" state.
  - "esc to cancel" also appears in gemini's working status area, so it is only
    trusted as a busy signal when no title is available at all.

States returned: "error" | "awaiting_permission" | "busy" | "idle" | "unknown".
"""

import re

ANSI = re.compile(r"\x1b\[[0-9;]*m")

# Screen dialogs that block until a human answers. Checked before the title:
# gemini keeps the title at "Ready" while these are up.
BLOCKED_PATTERNS = (
    "Do you trust this folder?",
    "Apply this change",
    "Allow execution",
    "waiting for user confirmation",
)
# API-key entry dialog footer — auth required, dispatch can't help. Deliberately
# narrow: the idle banner also mentions "/auth", which must NOT read as an error.
AUTH_PATTERN = re.compile(r"clear stored key", re.IGNORECASE)
TITLE_BUSY_PATTERN = re.compile(r"^✦|Working…")
TITLE_IDLE_PATTERN = re.compile(r"^◇|Ready")
SCREEN_BUSY_PATTERN = re.compile(r"esc to cancel", re.IGNORECASE)


def strip_ansi(text: str) -> str:
    return ANSI.sub("", text)


def classify_gemini_state(capture_plain: str, capture_with_ansi: str = "", pane_title: str = "") -> dict:
    plain = strip_ansi(capture_plain) if capture_plain else ""
    nonblank = [ln for ln in plain.splitlines() if ln.strip()]
    # gemini renders full-screen boxes; the live dialog/status area is the
    # bottom slice of the visible screen.
    tail = "\n".join(nonblank[-15:])

    # 1. Blocking dialogs — trust folder / tool approval. Screen text is the
    #    only source: the title still says "Ready" while these are up.
    for pat in BLOCKED_PATTERNS:
        if pat in tail:
            return {"state": "awaiting_permission", "detail": pat}

    # 2. API-key entry dialog — needs a human to authenticate.
    if AUTH_PATTERN.search(tail):
        return {"state": "error", "detail": "API key prompt — authenticate gemini first"}

    # 3. Title — the authoritative working/idle signal when present.
    title = (pane_title or "").strip()
    if title:
        if TITLE_BUSY_PATTERN.search(title):
            return {"state": "busy", "detail": title[:60]}
        if TITLE_IDLE_PATTERN.search(title):
            return {"state": "idle", "detail": ""}

    # 4. No usable title (OSC filtered by an outer terminal/mux) — screen only.
    if SCREEN_BUSY_PATTERN.search(tail):
        return {"state": "busy", "detail": "esc to cancel"}
    if nonblank:
        return {"state": "idle", "detail": ""}
    return {"state": "unknown", "detail": "no pane capture"}
