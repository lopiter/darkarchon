"""Unit tests for tmux scanner — mocks subprocess so no real tmux required."""

from unittest.mock import MagicMock, patch

from lib.tmux_scanner import list_llm_panes, scan_panes


def test_list_llm_panes_filters_by_process_name():
    # Format: pid attached win_active pane_active window_id pane_id process target window cwd
    fake_output = (
        "12345 1 1 1 @0 %0 claude alpha:1.0 alpha-window /Users/u/repo1\n"
        "12346 1 1 0 @1 %1 zsh    alpha:2.0 alpha-window /Users/u/repo2\n"
        "12347 0 0 1 @2 %2 claude beta:work-1.0 beta-window /Users/u/repo3\n"
        "12348 0 0 0 @3 %3 vim    beta:work-1.1 beta-window /Users/u/repo3\n"
    )
    with patch("lib.tmux_scanner.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=fake_output)
        panes = list_llm_panes(allowed_processes=("claude",))

    assert len(panes) == 2
    assert panes[0].target == "alpha:1.0"
    assert panes[0].process == "claude"
    assert panes[0].cwd == "/Users/u/repo1"
    assert panes[0].window_name == "alpha-window"
    assert panes[0].window_id == "@0"
    assert panes[0].pane_id == "%0"
    assert panes[0].focused is True  # attached + win_active + pane_active
    assert panes[1].target == "beta:work-1.0"
    assert panes[1].focused is False  # detached session


def test_list_llm_panes_extends_allowed_list():
    fake_output = (
        "12345 1 1 1 @0 %0 claude alpha:1.0 alpha-window /Users/u/repo1\n"
        "12346 1 1 0 @1 %1 codex  alpha:2.0 alpha-window /Users/u/repo2\n"
        "12347 0 0 1 @2 %2 gemini beta:work-1.0 beta-window /Users/u/repo3\n"
    )
    with patch("lib.tmux_scanner.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=fake_output)
        panes = list_llm_panes(allowed_processes=("claude", "codex", "gemini"))

    assert {p.process for p in panes} == {"claude", "codex", "gemini"}


def test_list_llm_panes_focused_requires_attached_active_window_and_pane():
    """`focused` is True only when the session is attached AND its window is the
    active one AND the pane is active in that window — the exact pane a client
    is currently looking at. Any zero drops focus."""
    fake_output = (
        "1 1 1 1 @4 %4 claude a:1.0 w /r\n"  # viewed: all three set
        "2 0 1 1 @5 %5 claude b:1.0 w /r\n"  # detached session
        "3 2 0 1 @6 %6 claude c:1.0 w /r\n"  # window not active (other window shown)
        "4 1 1 0 @7 %7 claude d:1.0 w /r\n"  # pane not active (split partner)
    )
    with patch("lib.tmux_scanner.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=fake_output)
        panes = list_llm_panes(allowed_processes=("claude",))

    by_target = {p.target: p.focused for p in panes}
    assert by_target == {
        "a:1.0": True,
        "b:1.0": False,
        "c:1.0": False,
        "d:1.0": False,
    }


def test_scan_panes_propagates_focused_flag():
    """scan_panes carries PaneInfo.focused into the reported worker dict so the
    hub/notify path can suppress alerts for the pane the user is viewing."""
    from lib.tmux_scanner import PaneInfo

    panes = [
        PaneInfo(pid="1", process="claude", target="x:0.0", cwd="/r", window_name="w", focused=True),
        PaneInfo(pid="2", process="claude", target="y:0.0", cwd="/r", window_name="w", focused=False),
    ]
    with patch("lib.tmux_scanner.list_llm_panes", return_value=panes):
        with patch("lib.tmux_scanner.capture_pane", return_value="❯\n─\n"):
            workers = scan_panes()

    by_target = {w["target"]: w["focused"] for w in workers}
    assert by_target == {"x:0.0": True, "y:0.0": False}


def test_list_llm_panes_returns_empty_when_tmux_fails():
    with patch("lib.tmux_scanner.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="no server")
        panes = list_llm_panes(allowed_processes=("claude",))

    assert panes == []


def test_list_llm_panes_includes_window_name_matches():
    """Panes whose window_name matches `window_names` are included even with non-LLM process."""
    fake_output = (
        "12345 1 1 1 @0 %0 zsh    sess:1.0 claude /Users/u/repo1\n"  # zsh in 'claude' window
        "12346 1 1 0 @1 %1 vim    sess:2.0 other /Users/u/repo2\n"  # vim in 'other' window
    )
    with patch("lib.tmux_scanner.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=fake_output)
        panes = list_llm_panes(allowed_processes=("claude",), window_names=("claude",))

    assert len(panes) == 1
    assert panes[0].window_name == "claude"
    assert panes[0].process == "zsh"


def test_scan_panes_routes_claude_and_codex_to_their_detectors():
    """scan_panes classifies a claude pane with the Claude detector (busy spinner)
    and a codex pane with the codex detector (Working(…) line), per process."""
    from lib.tmux_scanner import PaneInfo

    panes = [
        PaneInfo(pid="1", process="claude", target="x:0.0", cwd="/r1", window_name="random-window"),
        PaneInfo(pid="2", process="codex", target="x:1.0", cwd="/r2", window_name="random-window"),
    ]

    def fake_capture(target: str, with_ansi: bool):
        if target == "x:0.0":  # claude busy
            return "  ✽ Whisking…\n─\n❯\n─\n"
        # codex busy: Working(…) line + composer footer
        return (
            " Working (3s • Esc to interrupt)\n"
            "▌\n"
            " ⏎ send   ⌃J newline   ⌃T transcript   ⌃C quit\n"
        )

    with patch("lib.tmux_scanner.list_llm_panes", return_value=panes):
        with patch("lib.tmux_scanner.capture_pane", side_effect=fake_capture):
            workers = scan_panes()

    by_proc = {w["process"]: w for w in workers}
    assert by_proc["claude"]["state"] == "busy"
    # codex must now be classified (not dropped) and routed to the codex detector.
    assert by_proc["codex"]["state"] == "busy"


def test_scan_panes_codex_idle_pane_classified_idle():
    """A codex pane showing only its idle footer (composer visible, no Working
    line) classifies as idle — the footer alone is never a busy signal."""
    from lib.tmux_scanner import PaneInfo

    panes = [
        PaneInfo(pid="1", process="codex", target="x:0.0", cwd="/r", window_name="w"),
    ]

    def fake_capture(target: str, with_ansi: bool):
        return (
            "▌ Improve documentation in @filename\n"
            " ⏎ send   ⌃J newline   ⌃T transcript   ⌃C quit\n"
        )

    with patch("lib.tmux_scanner.list_llm_panes", return_value=panes):
        with patch("lib.tmux_scanner.capture_pane", side_effect=fake_capture):
            workers = scan_panes()

    assert workers[0]["process"] == "codex"
    assert workers[0]["state"] == "idle"


def test_scan_panes_registry_routes_node_process_to_codex():
    """A registered codex worker whose process shows as plain `node` (nvm-installed
    codex 0.135) must be discovered and routed to the codex detector via known_kinds
    — not misrouted to claude by its box-drawing `─`."""
    from lib.tmux_scanner import PaneInfo

    panes = [
        PaneInfo(pid="1", process="node", target="codextest:tc.0", cwd="/r", window_name="tc"),
    ]

    def fake_capture(target: str, with_ansi: bool):
        # codex 0.135 busy capture: box-drawing ─ (would trip claude marker) + Working line
        return (
            "╭─────────────────────────╮\n"
            "│ >_ OpenAI Codex (v0.135.0)│\n"
            "╰─────────────────────────╯\n"
            "• Working (2s • esc to interrupt)\n"
        )

    known = {"codextest:tc": "codex"}
    with patch("lib.tmux_scanner.list_llm_panes", return_value=panes):
        with patch("lib.tmux_scanner.capture_pane", side_effect=fake_capture):
            workers = scan_panes(known_kinds=known)

    assert workers[0]["process"] == "codex"
    assert workers[0]["state"] == "busy"


def test_list_llm_panes_discovers_registered_node_worker():
    """list_llm_panes includes a `node` pane (not normally an LLM candidate) when
    it matches a registered worker target in known_kinds."""
    fake_output = (
        "111 1 1 1 @8 %8 node codextest:tc.0 tc /r\n"          # codex-as-node, registered
        "222 1 1 0 @9 %9 node random:1.0 random-window /x\n"   # unrelated node, not registered
    )
    with patch("lib.tmux_scanner.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=fake_output)
        panes = list_llm_panes(known_kinds={"codextest:tc": "codex"})

    targets = [p.target for p in panes]
    assert "codextest:tc.0" in targets
    assert "random:1.0" not in targets


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


def test_list_llm_panes_matches_truncated_grok_binary_name():
    """grok's binary is `grok-macos-aarch64`; tmux truncates pane_current_command
    to 15 chars, so the pane must be found by prefix, not by exact name."""
    fake_output = (
        "12345 1 1 1 @0 %0 grok-macos-aarc alpha:1.0 alpha-window /Users/u/repo1\n"
        "12346 1 1 0 @1 %1 grokker         alpha:2.0 alpha-window /Users/u/repo2\n"
    )
    with patch("lib.tmux_scanner.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=fake_output)
        panes = list_llm_panes(allowed_processes=("claude", "codex", "grok"))

    assert [p.process for p in panes] == ["grok-macos-aarc"]


def test_scan_panes_routes_grok_process_to_grok_detector():
    """A native grok pane shares the ❯ composer with claude; the process name
    must win so it is classified by the grok detector, not the claude one."""
    from lib.tmux_scanner import PaneInfo

    panes = [PaneInfo(pid="1", process="grok-macos-aarc", target="g:0.0", cwd="/r", window_name="w")]
    with patch("lib.tmux_scanner.list_llm_panes", return_value=panes):
        with patch("lib.tmux_scanner.capture_pane", return_value="│ ❯ │\nShift+Tab:mode  │  Esc:cancel  │  Ctrl+.:shortcuts\n"):
            with patch("lib.tmux_scanner.capture_pane_title", return_value="⠧ - Responding - grok"):
                workers = scan_panes()

    assert workers[0]["process"] == "grok"
    assert workers[0]["state"] == "busy"
