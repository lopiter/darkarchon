"""Grok CLI (xAI "Grok Build") TUI state detector — pure function over
captured pane text + OSC title.

Counterpart to detectors/{claude,codex,gemini}.py for grok workers. Rules were
ported from herdr's grok detection manifest (evidence: Grok Build 0.2.101) and
re-verified live against grok 1.0.5 in a tmux pane (2026-08-23):

  - The OSC title is the primary working/idle signal. Idle: "grok" or
    "<session title> - grok" with no braille glyph. Working: a braille spinner
    prefix plus activity text, e.g. "⠧ - Waiting for response… - grok" or
    "⠦ - Sleep 6 seconds… - <session title> - grok". (1.0.5 verified.)
  - Working turns also render one live status line above the composer with a
    braille spinner and a trailing "[stop]" chip
    ("⠴ Sleep 6 seconds then print hi… 1.3s   5.0s ⇣19.8k [↓][stop]"), and the
    footer gains "Esc:cancel". Idle footers read "Shift+Tab:mode │ Ctrl+.:shortcuts"
    and never contain "Esc:cancel". (1.0.5 verified.)
  - The startup splash draws its logo with braille characters, so working
    rules anchor on the "[stop]" chip, never on a bare spinner glyph.
  - Permission prompts put "⚠ Action Required" in the title (blinks while the
    terminal is unfocused) and draw a "┃"-guttered option list
    ("┃  2 (○) Yes, proceed") with footer "1/3:select │ Ctrl+o:yolo │ Ctrl+c:cancel".
    (0.2.x evidence; 1.0.5 panes ran with auto-approve so this was not re-seen.)
  - ask-user-question dialogs draw the same option list ("┃  1 (○) Option A",
    "┃  z (○) Type your answer here") with footer "… │ Shift+x:dismiss" — and
    the title KEEPS its busy spinner while the dialog is up, so screen blocker
    rules must outrank the title. (1.0.5 verified.)
  - Background work clears the OSC busy title; the pinned top row then shows an
    animated chip with the running-task count ("⠋ MCP (4/5) │ …" style chrome
    also lives there). Ported from herdr's engine-3 rule, unverified on 1.0.5.

tmux exposes OSC 0/2 titles via #{pane_title} but does NOT retain OSC 9;4
progress, so herdr's osc_progress rules are intentionally absent here.

Auth failure / rate-limit screens are not known for grok (neither herdr nor the
1.0.5 probe produced one), so this detector never returns "error".

States returned: "awaiting_permission" | "awaiting_user" | "busy" | "idle" | "unknown".
"""

import re

ANSI = re.compile(r"\x1b\[[0-9;]*m")

BRAILLE = "⠀-⣿"

# ── title ──────────────────────────────────────────────────────────────────
TITLE_BLOCKED_PATTERN = re.compile(r"Action Required", re.IGNORECASE)
# "grok" alone, or anything ending in " - grok", with no braille spinner.
TITLE_IDLE_PATTERN = re.compile(r"(?:^| - )grok$")
TITLE_SPINNER_PATTERN = re.compile(f"[{BRAILLE}]")

# ── screen: blocked ────────────────────────────────────────────────────────
# Option rows of a permission / question dialog: "┃  2 (○) Yes, proceed",
# "┃  z (○) Type your answer here". The ┃ gutter is what separates these from
# ordinary numbered output.
OPTION_ROW_PATTERN = re.compile(r"^\s*┃\s+[0-9a-z]+\s+\([●○]\)\s", re.MULTILINE)
# Footer of a permission prompt (all three must appear).
PERMISSION_FOOTER_HINTS = (":select", "ctrl+o:yolo", "ctrl+c:cancel")
# Footer of an ask-user-question dialog. 0.2.x read "Tab:scrollback │
# Shift+x:dismiss", 1.0.5 reads "Tab:next answer │ Esc:scrollback │
# Shift+x:dismiss" — only the dismiss hint is stable across versions.
QUESTION_FOOTER_HINT = "shift+x:dismiss"
# Legacy (pre-0.2) permission scope selector.
LEGACY_PERMISSION_HINTS = ("yes, proceed", "no, reject")

# ── screen: working ────────────────────────────────────────────────────────
# Live status line: braille spinner, text, trailing [stop] chip (optionally
# preceded by a [↓] chip while a tool is streaming output).
STOP_CHIP_PATTERN = re.compile(f"^\\s*[{BRAILLE}]\\s.*\\[stop\\]\\s*$", re.MULTILINE)
ESC_CANCEL_HINT = "esc:cancel"
# Pinned top-row background-work chip: "<glyph> N │".
BACKGROUND_CHIP_PATTERN = re.compile(r"[⋅:⸬⁙.·]\s+[1-9][0-9]*\s+│")

# ── screen: idle ───────────────────────────────────────────────────────────
SHORTCUTS_HINT = "ctrl+.:shortcuts"


def strip_ansi(text: str) -> str:
    return ANSI.sub("", text)


def _question_vs_permission(tail_lower: str) -> str:
    """Both dialogs share the ┃ option-row shape; the footer tells them apart."""
    if all(h in tail_lower for h in PERMISSION_FOOTER_HINTS):
        return "awaiting_permission"
    if QUESTION_FOOTER_HINT in tail_lower:
        return "awaiting_user"
    # Option rows with neither footer visible (e.g. footer scrolled/clipped) —
    # treat as a permission prompt: both need a human, and permission is the
    # reading dispatch-safe handles most conservatively.
    return "awaiting_permission"


def classify_grok_state(capture_plain: str, capture_with_ansi: str = "", pane_title: str = "") -> dict:
    plain = strip_ansi(capture_plain) if capture_plain else ""
    nonblank = [ln for ln in plain.splitlines() if ln.strip()]
    # grok runs full-screen; dialogs, the status line, the composer and the
    # footer all live in the bottom slice of the visible screen.
    tail = "\n".join(nonblank[-20:])
    tail_lower = tail.lower()
    footer_lower = "\n".join(nonblank[-2:]).lower()
    title = (pane_title or "").strip()

    # 1. Blocking dialogs. Screen first: a question dialog leaves the busy
    #    spinner in the title, so the title alone would misreport it as busy.
    if OPTION_ROW_PATTERN.search(tail):
        kind = _question_vs_permission(footer_lower)
        return {"state": kind, "detail": "option dialog"}
    if all(h in footer_lower for h in PERMISSION_FOOTER_HINTS):
        return {"state": "awaiting_permission", "detail": "permission prompt"}
    if QUESTION_FOOTER_HINT in footer_lower:
        return {"state": "awaiting_user", "detail": "question dialog"}
    if all(h in tail_lower for h in LEGACY_PERMISSION_HINTS):
        return {"state": "awaiting_permission", "detail": "permission prompt"}
    if title and TITLE_BLOCKED_PATTERN.search(title):
        return {"state": "awaiting_permission", "detail": title[:80]}

    # 2. Background work chip on the pinned top row — grok drops its OSC busy
    #    title while background tasks run, so this must precede the title.
    if nonblank and BACKGROUND_CHIP_PATTERN.search(nonblank[0]):
        return {"state": "busy", "detail": "background work"}

    # 3. Title — authoritative working/idle signal when present.
    if title:
        if TITLE_IDLE_PATTERN.search(title) and not TITLE_SPINNER_PATTERN.search(title):
            return {"state": "idle", "detail": ""}
        return {"state": "busy", "detail": title[:60]}

    # 4. No usable title — screen only.
    m = STOP_CHIP_PATTERN.search(tail)
    if m:
        return {"state": "busy", "detail": m.group().strip()[:60]}
    if ESC_CANCEL_HINT in footer_lower:
        return {"state": "busy", "detail": "Esc:cancel"}
    if SHORTCUTS_HINT in footer_lower:
        return {"state": "idle", "detail": ""}
    if nonblank:
        return {"state": "idle", "detail": ""}
    return {"state": "unknown", "detail": "no pane capture"}
