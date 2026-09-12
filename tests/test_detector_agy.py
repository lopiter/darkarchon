"""Unit tests for the agy (Antigravity CLI) state detector — pure function tests.

Every fixture was captured live from agy 1.2.2 in a tmux pane (2026-09-12).
agy sets no OSC title, so the title argument is always the shell's leftover
(here: a hostname) and must never influence the result.
"""

from lib.detectors.agy import classify_agy_state

TITLE = "MacBook-Pro-2.local"


# ── idle ────────────────────────────────────────────────────────────────────
def test_fresh_pane_is_idle(load_fixture):
    r = classify_agy_state(load_fixture("agy_idle.txt"), "", TITLE)
    assert r["state"] == "idle"


def test_idle_after_a_turn_is_idle(load_fixture):
    """Finished turns leave their '> prompt' echo in scrollback — only the live
    composer above the footer may count as unsent."""
    r = classify_agy_state(load_fixture("agy_idle_after_turn.txt"), "", TITLE)
    assert r["state"] == "idle"


def test_typed_but_unsent_text_is_unsent(load_fixture):
    r = classify_agy_state(load_fixture("agy_unsent.txt"), "", TITLE)
    assert r["state"] == "unsent"
    assert "half typed" in r["detail"]


# ── busy ────────────────────────────────────────────────────────────────────
def test_running_command_is_busy(load_fixture):
    r = classify_agy_state(load_fixture("agy_busy.txt"), "", TITLE)
    assert r["state"] == "busy"
    assert "Running" in r["detail"]


def test_generating_is_busy(load_fixture):
    r = classify_agy_state(load_fixture("agy_busy_generating.txt"), "", TITLE)
    assert r["state"] == "busy"


def test_queued_follow_up_is_busy(load_fixture):
    """Text typed mid-turn is queued, not interrupting — the pane is still
    working and must not be dispatched to."""
    r = classify_agy_state(load_fixture("agy_queued.txt"), "", TITLE)
    assert r["state"] == "busy"
    assert r["detail"] == "queued message"


def test_spinner_line_without_footer_is_busy():
    plain = "> do it\n⠋  Thinking...\n"
    assert classify_agy_state(plain, "", TITLE)["state"] == "busy"


def test_background_task_chip_is_busy():
    plain = "> do it\n  DONE\n─────\n>\n─────\n· 2 tasks   Gemini 3.8 Flash · high\n"
    assert classify_agy_state(plain, "", TITLE)["state"] == "busy"


# ── blocked ─────────────────────────────────────────────────────────────────
def test_permission_dialog_is_awaiting_permission(load_fixture):
    """The footer still says 'esc to cancel' under the dialog, so the dialog
    must win over the busy footer."""
    r = classify_agy_state(load_fixture("agy_permission.txt"), "", TITLE)
    assert r["state"] == "awaiting_permission"


def test_legacy_permission_wording_is_awaiting_permission():
    plain = "Requesting permission for:\n   rm -rf build\nDo you want to proceed?\n> 1. Yes\n  2. No\nesc to cancel\n"
    assert classify_agy_state(plain, "", TITLE)["state"] == "awaiting_permission"


def test_trust_dialog_is_awaiting_permission(load_fixture):
    r = classify_agy_state(load_fixture("agy_trust_prompt.txt"), "", TITLE)
    assert r["state"] == "awaiting_permission"
    assert "trust" in r["detail"]


# ── edge ────────────────────────────────────────────────────────────────────
def test_blank_capture_is_unknown():
    assert classify_agy_state("", "", TITLE)["state"] == "unknown"
