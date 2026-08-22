"""Unit tests for the grok state detector — pure function tests.

Idle / busy / question-dialog fixtures were captured live from grok 1.0.5 in a
tmux pane (2026-08-23). The permission-prompt fixture is reconstructed from
herdr's manifest evidence (Grok Build 0.2.101): the 1.0.5 probe ran with
auto-approve and never showed one.
"""

from lib.detectors.grok import classify_grok_state


# ── title: the primary working/idle signal ──────────────────────────────────
def test_title_bare_grok_is_idle(load_fixture):
    plain = load_fixture("grok_idle.txt")
    assert classify_grok_state(plain, plain, "grok")["state"] == "idle"


def test_title_session_suffix_is_idle(load_fixture):
    plain = load_fixture("grok_idle_after_turn.txt")
    r = classify_grok_state(plain, plain, "Run sleep command then echo hi - grok")
    assert r["state"] == "idle"


def test_title_spinner_is_busy(load_fixture):
    plain = load_fixture("grok_busy.txt")
    r = classify_grok_state(plain, plain, "⠧ - Waiting for response… - grok")
    assert r["state"] == "busy"


def test_title_spinner_with_session_suffix_is_busy(load_fixture):
    plain = load_fixture("grok_busy.txt")
    r = classify_grok_state(plain, plain, "⠦ - Sleep 6 seconds then print hi… - Run sleep command - grok")
    assert r["state"] == "busy"


def test_title_action_required_is_awaiting_permission(load_fixture):
    plain = load_fixture("grok_idle_after_turn.txt")
    r = classify_grok_state(plain, plain, "⚠ Action Required - grok")
    assert r["state"] == "awaiting_permission"


def test_splash_braille_logo_is_not_busy(load_fixture):
    """The startup logo is drawn in braille; with an idle title it must read idle."""
    plain = load_fixture("grok_idle.txt")
    assert classify_grok_state(plain, plain, "grok")["state"] == "idle"
    assert classify_grok_state(plain, plain, "")["state"] == "idle"


# ── dialogs: screen outranks the title ──────────────────────────────────────
def test_question_dialog_is_awaiting_user_despite_busy_title(load_fixture):
    plain = load_fixture("grok_question.txt")
    r = classify_grok_state(plain, plain, "⠧ - Running: Ask: Do you prefer… - grok")
    assert r["state"] == "awaiting_user"


def test_permission_prompt_is_awaiting_permission(load_fixture):
    plain = load_fixture("grok_permission.txt")
    r = classify_grok_state(plain, plain, "⠧ - Running rm… - grok")
    assert r["state"] == "awaiting_permission"


def test_legacy_permission_scope_selector():
    plain = "Yes, proceed\nNo, reject\nUse ← → to choose permission whitelist scope\n"
    assert classify_grok_state(plain, plain, "grok")["state"] == "awaiting_permission"


# ── background work chip on the pinned top row ──────────────────────────────
def test_background_chip_is_busy_even_with_idle_title(load_fixture):
    plain = "  ~/work/repo  · 2 │ 6.8K / 500K\n" + load_fixture("grok_idle_after_turn.txt")
    assert classify_grok_state(plain, plain, "grok")["state"] == "busy"


# ── no title available: screen-only classification ──────────────────────────
def test_no_title_stop_chip_is_busy(load_fixture):
    plain = load_fixture("grok_busy.txt")
    assert classify_grok_state(plain, plain, "")["state"] == "busy"


def test_no_title_idle_footer_is_idle(load_fixture):
    plain = load_fixture("grok_idle_after_turn.txt")
    assert classify_grok_state(plain, plain, "")["state"] == "idle"


def test_blank_capture_is_unknown():
    assert classify_grok_state("", "", "")["state"] == "unknown"
