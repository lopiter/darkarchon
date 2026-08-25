"""migrate-orch-markers: rewrite live markers, delete dead, skip ambiguous."""

from pathlib import Path

from lib.migrate_orch_markers import (
    classify_tmux_lookup,
    parse_pane_key,
    plan_one,
    run,
)


def _write_marker(root: Path, team: str, text: str) -> Path:
    d = root / team
    d.mkdir(parents=True)
    p = d / "orchestrator.txt"
    p.write_text(text)
    (d / "workers-runtime.env").write_text("WORKER_a_TARGET=x:1\n")
    return p


def test_parse_pane_key():
    assert parse_pane_key("3hour:1.1") == ("3hour", "1", "1")
    assert parse_pane_key("dark:2.0") == ("dark", "2", "0")
    assert parse_pane_key("3hour:3hour") is None
    assert parse_pane_key("") is None


def test_rewrite_live_index_marker(tmp_path):
    p = _write_marker(tmp_path, "3hour", "3hour:1.1\n")

    def resolve(session, win, pane):
        assert (session, win, pane) == ("3hour", "1", "1")
        return {"status": "live", "pane": "3hour:1.1", "window_id": "@14",
                "window_name": "3hour", "process": "claude"}

    item = plan_one(p, resolve_index=resolve, id_live=lambda _w: False)
    assert item["action"] == "rewrite"
    assert item["new"] == "3hour:1.1 @14"


def test_delete_dead_index_marker(tmp_path):
    p = _write_marker(tmp_path, "gone", "gone:1.1\n")

    def resolve(session, win, pane):
        return {"status": "dead"}

    item = plan_one(p, resolve_index=resolve, id_live=lambda _w: False)
    assert item["action"] == "delete"


def test_skip_ambiguous_tmux_fallback(tmp_path):
    p = _write_marker(tmp_path, "3hour", "3hour:9.1\n")

    def resolve(session, win, pane):
        return {
            "status": "ambiguous",
            "detail": "tmux returned 3hour:1.1 for 3hour:9.1 (active-window fallback?)",
        }

    item = plan_one(p, resolve_index=resolve, id_live=lambda _w: False)
    assert item["action"] == "skip"
    assert p.read_text() == "3hour:9.1\n"  # untouched


def test_already_new_and_live_is_ok(tmp_path):
    p = _write_marker(tmp_path, "3hour", "3hour:1.1 @14\n")
    item = plan_one(p, resolve_index=lambda *_a: {"status": "dead"},
                    id_live=lambda w: w == "@14")
    assert item["action"] == "ok"


def test_already_new_but_id_gone_deletes(tmp_path):
    p = _write_marker(tmp_path, "3hour", "3hour:1.1 @14\n")
    item = plan_one(p, resolve_index=lambda *_a: {"status": "dead"},
                    id_live=lambda _w: False)
    assert item["action"] == "delete"


def test_dry_run_does_not_touch_files(tmp_path):
    live = _write_marker(tmp_path, "live", "live:1.1\n")
    dead = _write_marker(tmp_path, "dead", "dead:1.1\n")

    def resolve(session, win, pane):
        if session == "live":
            return {"status": "live", "pane": "live:1.1", "window_id": "@5",
                    "window_name": "live", "process": "claude"}
        return {"status": "dead"}

    plans = run(tmp_path, apply=False, resolve_index=resolve,
                id_live=lambda _w: False)
    assert {i["action"] for i in plans} == {"rewrite", "delete"}
    assert live.read_text() == "live:1.1\n"
    assert dead.read_text() == "dead:1.1\n"


def test_apply_rewrites_and_deletes(tmp_path):
    live = _write_marker(tmp_path, "live", "live:1.1\n")
    dead = _write_marker(tmp_path, "dead", "dead:1.1\n")
    skip = _write_marker(tmp_path, "skip", "skip:9.1\n")

    def resolve(session, win, pane):
        if session == "live":
            return {"status": "live", "pane": "live:1.1", "window_id": "@5",
                    "window_name": "live", "process": "claude"}
        if session == "skip":
            return {"status": "ambiguous", "detail": "fallback"}
        return {"status": "dead"}

    run(tmp_path, apply=True, resolve_index=resolve, id_live=lambda _w: False)
    assert live.read_text() == "live:1.1 @5\n"
    assert not dead.exists()
    assert skip.read_text() == "skip:9.1\n"


def test_empty_tmux_output_rc0_is_dead():
    hit = classify_tmux_lookup("   \n", 0, "hotel", "1", "1")
    assert hit["status"] == "dead"
    hit = classify_tmux_lookup("|||||\n", 0, "hotel", "1", "1")
    assert hit["status"] == "dead"


def test_delete_empty_tmux_session_marker(tmp_path):
    p = _write_marker(tmp_path, "hotel", "hotel:1.1\n")

    def resolve(session, win, pane):
        return classify_tmux_lookup("   \n", 0, session, win, pane)

    item = plan_one(p, resolve_index=resolve, id_live=lambda _w: False)
    assert item["action"] == "delete"


def test_delete_non_agent_pane(tmp_path):
    p = _write_marker(tmp_path, "hermes", "hermes:1.1\n")

    def resolve(session, win, pane):
        return {"status": "live", "pane": "hermes:1.1", "window_id": "@3",
                "window_name": "python3.11", "process": "python3.11"}

    item = plan_one(p, resolve_index=resolve, id_live=lambda _w: False)
    assert item["action"] == "delete"
    assert "not an agent" in item["detail"]


def test_delete_registered_staff(tmp_path):
    p = _write_marker(tmp_path, "3hour", "3hour:1.1\n")
    (p.parent / "workers-runtime.env").write_text(
        "WORKER_ui_NAME=website-ui\n"
        "WORKER_ui_TARGET=3hour:website-ui\n"
        "WORKER_ui_ROLE=website-ui\n"
        "WORKER_ui_WINDOW_ID=@11\n"
    )

    def resolve(session, win, pane):
        return {"status": "live", "pane": "3hour:1.1", "window_id": "@11",
                "window_name": "website-ui", "process": "claude"}

    item = plan_one(p, resolve_index=resolve, id_live=lambda _w: False)
    assert item["action"] == "delete"
    assert "staff" in item["detail"]


def test_rewrite_real_orchestrator(tmp_path):
    p = _write_marker(tmp_path, "voc-2", "voc-2:2.1\n")
    (p.parent / "workers-runtime.env").write_text(
        "WORKER_voc_2_NAME=voc-2\n"
        "WORKER_voc_2_TARGET=voc-2:voc-2\n"
        "WORKER_voc_2_ROLE=orchestrator\n"
        "WORKER_voc_2_WINDOW_ID=@9\n"
    )

    def resolve(session, win, pane):
        return {"status": "live", "pane": "voc-2:2.1", "window_id": "@9",
                "window_name": "voc-2", "process": "claude"}

    item = plan_one(p, resolve_index=resolve, id_live=lambda _w: False)
    assert item["action"] == "rewrite"
    assert item["new"] == "voc-2:2.1 @9"
