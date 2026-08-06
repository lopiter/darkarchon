"""Unit tests for Claude state detector — pure function tests with fixture captures."""

from lib.detectors.claude import classify_claude_state


def test_idle_when_separator_above_prompt_has_no_active_marker(load_fixture):
    plain = load_fixture("claude_idle.txt")
    ansi = plain  # idle doesn't need ANSI distinction
    result = classify_claude_state(plain, ansi)
    assert result["state"] == "idle"


def test_busy_when_active_spinner_above_prompt(load_fixture):
    plain = load_fixture("claude_busy.txt")
    ansi = plain
    result = classify_claude_state(plain, ansi)
    assert result["state"] == "busy"
    assert "Whisking" in result["detail"]


def test_busy_when_korean_gerund_above_prompt(load_fixture):
    """Recent Claude Code TUI localizes the spinner verb — a Korean worker
    reports e.g. "작성 중 …" instead of "Writing…". Detector must still
    classify as busy."""
    plain = load_fixture("claude_busy_korean.txt")
    ansi = plain
    result = classify_claude_state(plain, ansi)
    assert result["state"] == "busy"


def test_compacting_preempts_busy_classification(load_fixture):
    plain = load_fixture("claude_compacting.txt")
    ansi = plain
    result = classify_claude_state(plain, ansi)
    assert result["state"] == "compacting"


def test_rate_limited_when_usage_limit_banner_present(load_fixture):
    plain = load_fixture("claude_rate_limited.txt")
    ansi = plain
    result = classify_claude_state(plain, ansi)
    assert result["state"] == "rate_limited"


def test_typed_when_prompt_has_plain_text_after_caret(load_fixture):
    ansi = load_fixture("claude_typed_real.txt")
    plain = ansi
    result = classify_claude_state(plain, ansi)
    assert result["state"] == "typed"
    assert "hello world" in result["detail"]


def test_idle_when_prompt_has_only_dim_placeholder(load_fixture):
    ansi = load_fixture("claude_typed_dim.txt")
    plain = ansi
    result = classify_claude_state(plain, ansi)
    assert result["state"] == "idle"


# ── shells_running flag ──────────────────────────────────────────────────────
# Real capture (2026-08-06): worker ran `sleep 20` in a background shell — the
# turn ended (Stop hook fired), and the live status area shows
# "✻ Cooked for 18s · 1 shell still running" over an empty prompt. The screen
# alone can't tell this from a mid-turn foreground wait, so the detector stays
# idle but raises shells_running; the resolver combines it with the hook state.

def test_shells_running_flag_set_from_live_status_area(load_fixture):
    plain = load_fixture("claude_shells_running.txt")
    result = classify_claude_state(plain, plain)
    assert result["state"] == "idle"
    assert result["shells_running"] is True


def test_shells_running_absent_on_plain_idle(load_fixture):
    plain = load_fixture("claude_idle.txt")
    result = classify_claude_state(plain, plain)
    assert result["state"] == "idle"
    assert not result.get("shells_running")


def test_stale_scrollback_shells_text_does_not_set_flag():
    """The 'still running' text lingers in scrollback long after the shells
    exit — only the activity area right above the prompt separator counts."""
    plain = (
        "✻ Cooked for 27s · 2 shells still running\n"
        "⏺ done with that task\n"
        "⏺ and something newer happened after\n"
        "────────────\n"
        "❯ \n"
        "────────────\n"
    )
    result = classify_claude_state(plain, plain)
    assert result["state"] == "idle"
    assert not result.get("shells_running")
