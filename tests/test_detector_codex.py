"""Unit tests for the Codex state detector — pure function tests with fixture
captures. Fixture strings are real `tmux capture-pane -p` output from codex-cli
0.34.0 (verified empirically by omc).

Codex differs from Claude: the input composer (▌) and footer (⏎ send …) stay
visible even while working, so busy is detected by the separate
"Working (Ns • Esc to interrupt)" line, NOT by the absence of a prompt.
"""

from lib.detectors.codex import classify_codex_state


def test_idle_when_footer_present_and_no_working_line(load_fixture):
    plain = load_fixture("codex_idle.txt")
    result = classify_codex_state(plain, plain)
    assert result["state"] == "idle"


def test_busy_when_working_line_present(load_fixture):
    plain = load_fixture("codex_busy.txt")
    result = classify_codex_state(plain, plain)
    assert result["state"] == "busy"


def test_busy_with_multi_digit_seconds(load_fixture):
    plain = load_fixture("codex_busy_long.txt")
    result = classify_codex_state(plain, plain)
    assert result["state"] == "busy"


def test_error_when_token_refresh_fails(load_fixture):
    """A visible 401 / stream error means codex can't make progress — surface it
    as 'error' so dispatch-safe refuses with a 'codex not logged in' message."""
    plain = load_fixture("codex_auth_error.txt")
    result = classify_codex_state(plain, plain)
    assert result["state"] == "error"


def test_auth_error_preempts_busy_and_idle(load_fixture):
    """Even though the auth-error capture also shows the idle footer, the error
    classification must win."""
    plain = load_fixture("codex_auth_error.txt")
    result = classify_codex_state(plain, plain)
    assert result["state"] == "error"


def test_footer_alone_is_not_busy(load_fixture):
    """The footer (⏎ send …) is visible during busy too, so it must never be
    used as a busy signal on its own."""
    plain = load_fixture("codex_idle.txt")
    result = classify_codex_state(plain, plain)
    assert result["state"] != "busy"


def test_blank_capture_is_not_busy_or_error():
    result = classify_codex_state("", "")
    assert result["state"] in ("idle", "unknown")
    assert result["state"] not in ("busy", "error")


# ── codex-cli 0.135.0 (real captures; TUI changed substantially from 0.34) ──
# 0.135 dropped the "⏎ send …" footer and shows an "OpenAI Codex (vX)" banner;
# busy uses lowercase "esc to interrupt". These guard against version drift.

def test_v135_idle_classified_idle(load_fixture):
    plain = load_fixture("codex_v135_idle.txt")
    result = classify_codex_state(plain, plain)
    assert result["state"] == "idle"


def test_v135_busy_classified_busy(load_fixture):
    plain = load_fixture("codex_v135_busy.txt")
    result = classify_codex_state(plain, plain)
    assert result["state"] == "busy"
