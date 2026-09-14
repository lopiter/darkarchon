"""Gemini CLI TUI state detector — pure function over captured pane text + title.

Counterpart to detectors/{claude,codex,grok}.py for gemini-cli workers. All
signals live-verified on gemini-cli against a real tmux pane (2026-08-06,
re-checked on 0.59.0 2026-09-12):

  - The OSC pane title is the primary signal — gemini toggles it through a full
    prompt cycle: "◇  Ready (<dir>)" while idle ↔ "✦  Working… (<dir>)" while
    generating. (Unlike Claude Code, whose title does not toggle with state.)
  - Blocked dialogs keep the title at "◇ Ready", so blocked must come from
    screen text: the trust-folder dialog ("Do you trust this folder?" — 0.59
    reworded it "Do you trust the files in this folder?") and tool approvals
    ("Apply this change" / "Allow execution" / "Do you want to proceed" boxes).
  - A missing/expired API key opens a key-entry dialog whose footer reads
    "Esc to cancel, Ctrl+C to clear stored key" — the worker can't progress
    until a human intervenes, same contract as codex's auth "error" state.
    0.59 also shows an auth-method picker ("How would you like to authenticate")
    when no login is on file or the Google token can't be refreshed; same
    error contract.
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
    "Do you trust the files in this folder?",
    "Apply this change",
    "Allow execution",
    "Do you want to proceed",
    "waiting for user confirmation",
)
# API-key entry dialog footer / auth-method picker — auth required, dispatch
# can't help. Deliberately narrow: the idle banner also mentions "/auth" and
# "Authenticated with …", which must NOT read as an error.
AUTH_PATTERN = re.compile(r"clear stored key|How would you like to authenticate", re.IGNORECASE)
TITLE_BUSY_PATTERN = re.compile(r"^✦|Working…")
TITLE_IDLE_PATTERN = re.compile(r"^◇|Ready")
SCREEN_BUSY_PATTERN = re.compile(r"esc to cancel", re.IGNORECASE)


def strip_ansi(text: str) -> str:
    return ANSI.sub("", text)


def classify_gemini_state(capture_plain: str, capture_with_ansi: str = "", pane_title: str = "") -> dict:
    plain = strip_ansi(capture_plain) if capture_plain else ""
    nonblank = [ln for ln in plain.splitlines() if ln.strip()]
    # gemini renders full-screen boxes; the live dialog/status area is the
    # bottom slice of the visible screen. Dialog boxes pad themselves with
    # empty "│ … │" rows (nonblank to us), and the auth picker is ~20 rows
    # tall with its question at the top, so the dialog window is wider than
    # the status one.
    tail = "\n".join(nonblank[-30:])

    # 1. Blocking dialogs — trust folder / tool approval. Screen text is the
    #    only source: the title still says "Ready" while these are up.
    for pat in BLOCKED_PATTERNS:
        if pat in tail:
            return {"state": "awaiting_permission", "detail": pat}

    # 2. API-key entry dialog — needs a human to authenticate.
    if AUTH_PATTERN.search(tail):
        return {"state": "error", "detail": "auth dialog — authenticate gemini first"}

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
