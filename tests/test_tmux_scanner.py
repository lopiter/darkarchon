"""Unit tests for tmux scanner — mocks subprocess so no real tmux required."""

from unittest.mock import MagicMock, patch

from lib.tmux_scanner import list_llm_panes, scan_panes


def test_list_llm_panes_filters_by_process_name():
    fake_output = (
        "12345 claude alpha:1.0 alpha-window /Users/u/repo1\n"
        "12346 zsh    alpha:2.0 alpha-window /Users/u/repo2\n"
        "12347 claude beta:work-1.0 beta-window /Users/u/repo3\n"
        "12348 vim    beta:work-1.1 beta-window /Users/u/repo3\n"
    )
    with patch("lib.tmux_scanner.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=fake_output)
        panes = list_llm_panes(allowed_processes=("claude",))

    assert len(panes) == 2
    assert panes[0].target == "alpha:1.0"
    assert panes[0].process == "claude"
    assert panes[0].cwd == "/Users/u/repo1"
    assert panes[0].window_name == "alpha-window"
    assert panes[1].target == "beta:work-1.0"


def test_list_llm_panes_extends_allowed_list():
    fake_output = (
        "12345 claude alpha:1.0 alpha-window /Users/u/repo1\n"
        "12346 codex  alpha:2.0 alpha-window /Users/u/repo2\n"
        "12347 gemini beta:work-1.0 beta-window /Users/u/repo3\n"
    )
    with patch("lib.tmux_scanner.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=fake_output)
        panes = list_llm_panes(allowed_processes=("claude", "codex", "gemini"))

    assert {p.process for p in panes} == {"claude", "codex", "gemini"}


def test_list_llm_panes_returns_empty_when_tmux_fails():
    with patch("lib.tmux_scanner.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="no server")
        panes = list_llm_panes(allowed_processes=("claude",))

    assert panes == []


def test_list_llm_panes_includes_window_name_matches():
    """Panes whose window_name matches `window_names` are included even with non-LLM process."""
    fake_output = (
        "12345 zsh    sess:1.0 claude /Users/u/repo1\n"  # zsh in 'claude' window
        "12346 vim    sess:2.0 other /Users/u/repo2\n"  # vim in 'other' window
    )
    with patch("lib.tmux_scanner.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=fake_output)
        panes = list_llm_panes(allowed_processes=("claude",), window_names=("claude",))

    assert len(panes) == 1
    assert panes[0].window_name == "claude"
    assert panes[0].process == "zsh"


def test_scan_panes_attaches_state_for_claude_only():
    """scan_panes calls list_llm_panes + capture_pane + detector per pane."""
    from lib.tmux_scanner import PaneInfo

    panes = [
        PaneInfo(pid="1", process="claude", target="x:0.0", cwd="/r1", window_name="random-window"),
        PaneInfo(pid="2", process="codex", target="x:1.0", cwd="/r2", window_name="random-window"),
    ]

    def fake_capture(target: str, with_ansi: bool):
        return "  ✽ Whisking…\n─\n❯\n─\n" if not with_ansi else "❯\n"

    with patch("lib.tmux_scanner.list_llm_panes", return_value=panes):
        with patch("lib.tmux_scanner.capture_pane", side_effect=fake_capture):
            workers = scan_panes()

    by_proc = {w["process"]: w for w in workers}
    assert by_proc["claude"]["state"] == "busy"
    # codex is not in allowed_processes default, and window_name doesn't match
    # — should be dropped by the defensive `continue` branch.
    assert "codex" not in by_proc


def test_scan_panes_window_name_marked_pane_shows_unknown_when_no_claude_marker():
    """A pane marked via window_name but without Claude TUI marker shows as unknown."""
    from lib.tmux_scanner import PaneInfo

    panes = [
        PaneInfo(pid="1", process="zsh", target="s:0.0", cwd="/r", window_name="claude"),
    ]
    with patch("lib.tmux_scanner.list_llm_panes", return_value=panes):
        with patch("lib.tmux_scanner.capture_pane", return_value="just a shell prompt $\n"):
            workers = scan_panes(window_names=("claude",))

    assert len(workers) == 1
    assert workers[0]["state"] == "unknown"
    assert workers[0]["window_name"] == "claude"
