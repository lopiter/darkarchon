"""Unit tests for the gemini state detector — pure function tests.

All signals were live-verified against a real gemini-cli pane in tmux
(2026-08-06): the OSC title toggles "◇  Ready (tmp)" ↔ "✦  Working… (tmp)"
through a full prompt cycle, while blocking dialogs (trust folder, API key)
keep the title at Ready and are only visible as screen text.
"""

from lib.detectors.gemini import classify_gemini_state


# ── title: the primary working/idle signal ──────────────────────────────────
def test_title_working_is_busy(load_fixture):
    plain = load_fixture("gemini_idle.txt")
    result = classify_gemini_state(plain, plain, "✦  Working… (tmp)")
    assert result["state"] == "busy"


def test_title_ready_is_idle(load_fixture):
    plain = load_fixture("gemini_idle.txt")
    result = classify_gemini_state(plain, plain, "◇  Ready (tmp)")
    assert result["state"] == "idle"


def test_idle_banner_auth_mention_is_not_error(load_fixture):
    """The idle banner says 'Authenticated with gemini-api-key /auth' — the
    /auth hint must not read as an auth failure."""
    plain = load_fixture("gemini_idle.txt")
    result = classify_gemini_state(plain, plain, "◇  Ready (tmp)")
    assert result["state"] == "idle"


# ── blocking dialogs: screen text only (title stays at Ready) ───────────────
def test_trust_folder_dialog_is_awaiting_permission(load_fixture):
    plain = load_fixture("gemini_trust_prompt.txt")
    result = classify_gemini_state(plain, plain, "◇  Ready (tmp)")
    assert result["state"] == "awaiting_permission"
    assert "trust" in result["detail"].lower()


def test_api_key_dialog_is_error(load_fixture):
    plain = load_fixture("gemini_authkey_prompt.txt")
    result = classify_gemini_state(plain, plain, "◇  Ready (tmp)")
    assert result["state"] == "error"


# ── no title available: screen-only classification ──────────────────────────
def test_no_title_esc_to_cancel_is_busy():
    plain = "✦ Generating response\n(esc to cancel)\n"
    result = classify_gemini_state(plain, plain, "")
    assert result["state"] == "busy"


def test_no_title_plain_screen_is_idle(load_fixture):
    plain = load_fixture("gemini_idle.txt")
    result = classify_gemini_state(plain, plain, "")
    assert result["state"] == "idle"


def test_blank_capture_is_unknown():
    result = classify_gemini_state("", "", "")
    assert result["state"] == "unknown"
