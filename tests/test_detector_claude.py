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


# ── busy must survive notice lines below the spinner ─────────────────────────
# Real failure (2026-08-09, live capture): the spinner was on screen at
# "✶ Kneading… (3m 8s · ↓ 11.8k tokens)" while the two lines below it were a
# usage tip and the auto-update banner. The detector only read the last two
# non-blank lines of the activity area, so the spinner fell outside the window
# and a working session scraped as idle. That matters beyond the dashboard:
# worker_state.synthesize self-heals hook=busy to idle when the scrape says
# idle, so a genuinely busy worker became dispatchable mid-turn.

def test_busy_when_notice_lines_sit_between_spinner_and_prompt(load_fixture):
    plain = load_fixture("claude_busy_notices_below_spinner.txt")
    result = classify_claude_state(plain, plain)
    assert result["state"] == "busy"
    assert "Kneading" in result["detail"]


# ── permission dialogs are not idle ──────────────────────────────────────────
# Real capture (2026-08-09): a worker blocked on a tool-approval dialog. The
# dialog replaces the input prompt, and its menu row starts with the same "❯"
# glyph, so the typed-input check read "1. Yes" as user-typed text and the
# worker reported unsent — whose documented remedy is `dispatch-safe --force`,
# a BSpace burst aimed at a live approval dialog.

def test_awaiting_permission_when_approval_dialog_is_open(load_fixture):
    plain = load_fixture("claude_awaiting_permission.txt")
    result = classify_claude_state(plain, plain)
    assert result["state"] == "awaiting_permission"


def test_trust_prompt_is_awaiting_permission():
    """The one-time folder-trust prompt has the same modal shape and was
    reported as unsent for the same reason."""
    plain = (
        " Quick safety check: Is this a project you created or one you trust?\n"
        " ❯ 1. Yes, I trust this folder\n"
        "   2. No, exit\n"
        " Enter to confirm · Esc to cancel\n"
    )
    result = classify_claude_state(plain, plain)
    assert result["state"] == "awaiting_permission"


def test_answered_dialog_in_scrollback_does_not_mask_the_live_prompt():
    """Once answered, the dialog stays visible but a real prompt is drawn
    below it. The live input prompt is the last ❯ line, so the worker is idle
    again — a stale dialog must not pin it to awaiting_permission."""
    plain = (
        " Do you want to proceed?\n"
        " ❯ 1. Yes\n"
        "   2. No\n"
        "⏺ Ran the command.\n"
        "────────────\n"
        "❯ \n"
        "────────────\n"
    )
    result = classify_claude_state(plain, plain)
    assert result["state"] == "idle"
