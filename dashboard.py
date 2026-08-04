#!/usr/bin/env python3
"""Team dashboard hub — multi-host status aggregator.

Agents (one per PC) POST scan results here; this hub stores them in memory and
serves a unified read-only web UI.
"""
from __future__ import annotations

import argparse
import http.server
import json
import os
import queue
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))  # allow `from lib.xxx import ...`

from lib.hub_store import HostStateStore  # noqa: E402
from lib.sse import SseBroker  # noqa: E402
from lib.task_store import TaskStore  # noqa: E402
from lib.team_index import classify, discover_teams, iso_to_epoch  # noqa: E402

# Cache TaskStore instances by db path — sqlite connections are short-lived
# per query but the schema-init is idempotent, so reusing the wrapper avoids
# re-running PRAGMA on every poll.
_TASK_STORE_CACHE: dict[str, TaskStore] = {}


def _task_store(state_dir: Path | None = None) -> TaskStore:
    """Resolve a TaskStore for the given state dir (default: hub's own STATE_DIR)."""
    base = state_dir if state_dir is not None else STATE_DIR
    key = str(base)
    if key not in _TASK_STORE_CACHE:
        _TASK_STORE_CACHE[key] = TaskStore(base / "tasks.db")
    return _TASK_STORE_CACHE[key]

# ─── Globals (overridable via CLI for multi-team support) ───────────────────
STATE_DIR = Path(os.environ.get("STATE_DIR") or (Path.home() / ".team"))
# Parent of every team's state dir. The hub serves the whole host, not just the
# team it was launched from, so team discovery starts here rather than at
# STATE_DIR. Overridden by --state-root; STATE_DIR.parent is only a fallback and
# is wrong when the hub itself runs from a nested worktree state dir.
STATE_ROOT = STATE_DIR.parent
SESSION_NAME = "team"
STORE: HostStateStore = HostStateStore(stale_after_seconds=30)
BROKER = SseBroker()

# ─── Question watcher state ─────────────────────────────────────────────────
_question_watcher_seen: set[str] = set()
_question_watcher_lock = threading.Lock()


def _question_watcher_loop():
    """Background thread: every 3s, scan questions dir for new pending questions and publish events."""
    questions_dir = STATE_DIR / "questions"
    while True:
        try:
            if questions_dir.is_dir():
                current = set()
                for f in questions_dir.glob("*.json"):
                    current.add(f.stem)
                with _question_watcher_lock:
                    new_ids = current - _question_watcher_seen
                    _question_watcher_seen.update(current)
                for qid in new_ids:
                    qf = questions_dir / f"{qid}.json"
                    try:
                        qdata = json.loads(qf.read_text())
                    except Exception:
                        continue
                    if qdata.get("status") == "pending":
                        BROKER.publish(
                            {
                                "type": "new_question",
                                "question_id": qdata.get("question_id"),
                                "from_worker": qdata.get("from_worker"),
                                "body": (qdata.get("body") or "")[:140],
                            }
                        )
        except Exception:
            pass
        time.sleep(3)


# ─── Task lookup (still file-based for click-through) ───────────────────────


def list_recent_tasks(worker_name: str, n: int = 3) -> list:
    rows = _task_store().list(worker=worker_name, limit=n)
    return [
        {
            "id": r["id"],
            "status": r["status"],
            "dispatched_at": r["dispatched_at"],
            "completed_at": r["completed_at"],
        }
        for r in rows
    ]


def last_activity(worker_name: str) -> str | None:
    return _task_store().last_activity_at(worker_name)


def humanize_age(iso_ts: str | None) -> str:
    if not iso_ts:
        return "—"
    try:
        ts = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    except Exception:
        return iso_ts
    delta = datetime.now(timezone.utc) - ts
    s = int(delta.total_seconds())
    if s < 0:
        return "just now"
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60}m ago"
    if s < 86400:
        return f"{s // 3600}h ago"
    return f"{s // 86400}d ago"


ACTIVE_DISPATCH_MAX_AGE_SEC = 300  # tasks older than this are treated as stale and ignored

# Aging thresholds, overridden from config.env via --dormant-days/--stale-days.
TEAM_DORMANT_DAYS = 7
TEAM_STALE_DAYS = 30


def _live_team_keys(workers: list) -> set:
    """(host, team) pairs that have at least one worker actually running.

    A reported worker is not the same as a live one. Registry entries outlive
    their panes, so a team can report several workers and have every one of
    them dead — that team has not been touched since whenever those panes
    died, and grading it 'live' both overstates it and hides its real age.

    Keyed by host as well as name because a team name is only unique within a
    host; two machines can each have an unrelated `voc`.
    """
    return {
        (w.get("host", ""), w["team_name"])
        for w in workers
        if w.get("team_name") and w.get("state") != "dead"
    }


def _teams_payload(live_keys: set) -> list:
    """Every host's team index, aged and tiered at serve time.

    Rows arrive from the hosts that own the state dirs — the hub cannot read
    another machine's disk, and reading its own would attribute those teams to
    whichever host happens to share a directory name.

    Hosts report facts (when a team was last active, by which signal); tiering
    is applied here because the thresholds are the hub's configuration. Idle is
    recomputed from the absolute timestamp rather than trusting the reported
    figure, which would freeze if an agent stopped reporting.
    """
    now = time.time()
    out = []
    for row in STORE.get_all_teams():
        epoch = iso_to_epoch(row.get("last_activity_at"))
        idle = None if epoch is None else max(0, int(now - epoch))
        key = (row.get("host", ""), row["name"])
        out.append(
            {
                **row,
                "idle_seconds": idle,
                "tier": classify(
                    idle,
                    is_live=key in live_keys,
                    worker_count=row.get("workers", 0),
                    dormant_days=TEAM_DORMANT_DAYS,
                    stale_days=TEAM_STALE_DAYS,
                ),
            }
        )
    out.sort(key=lambda r: (r["idle_seconds"] is None, r["idle_seconds"] or 0))
    return out


def _team_names(state_dirs: list | None = None) -> set:
    """Set of known team names. Pass `state_dirs` inside a loop to avoid
    re-walking the state root once per iteration."""
    return {team for _d, team in (state_dirs if state_dirs is not None else _all_state_dirs())}


def _team_for_session(session: str, known: set | None = None) -> str | None:
    """Return the team name whose SESSION_NAME equals `session`, or None.

    This is the most accurate signal of team membership — a worker's tmux
    session directly tells us which hub team it belongs to. Used to break
    ties when the same worker name exists in multiple worktree registries.

    `known` lets a caller in a hot loop pass a precomputed name set; without it
    every call re-walks the state root.
    """
    if not session:
        return None
    if known is None:
        known = _team_names()
    if session in known:
        return session
    return None
    return None


def _all_state_dirs() -> list:
    """Every team state dir on this host.

    Layout, both of which occur:
      <root>/<team>/                <-- flat team (config.env's STATE_DIR)
      <root>/<team>/<sub>/          <-- worktree team nested under it

    A directory counts as a team state dir when it contains
    workers-runtime.env. The hub's own STATE_DIR is always included, so a
    freshly started hub still reports its own team before anything spawns.

    Sweeping from STATE_ROOT rather than STATE_DIR is what lets the hub see
    teams other than the one it was launched from: dashboard.sh starts it with
    whatever DARKARCHON_TEAM the shell had, but the agent reports every pane on
    the machine, so a hub anchored to one team could not name the rest.

    Team names mirror the SESSION_NAME each team's own scripts resolve —
    config.env sets STATE_DIR=<root>/<SESSION_NAME>, so a flat dir's name is its
    team name, and a worktree sub-dir is '<team>-<sub>'.

    Returns: list of (state_dir_path, team_name).
    """
    out = [(STATE_DIR, SESSION_NAME)]
    seen = {STATE_DIR}
    for state_dir, name in discover_teams(STATE_ROOT):
        if state_dir in seen:
            continue
        # The hub's own dir keeps the --session-name it was started with, and
        # anything nested under it inherits that rather than the folder name.
        if state_dir.parent == STATE_DIR:
            name = f"{SESSION_NAME}-{state_dir.name}"
        out.append((state_dir, name))
        seen.add(state_dir)
    return out


def _active_dispatches_index() -> dict:
    """Scan running task json files across all team dirs.

    Same (sender, recipient) pair is recorded only once per direction.
    Stale tasks (running > ACTIVE_DISPATCH_MAX_AGE_SEC) ignored.

    Returns:
        {
          "by_recipient": {worker_name: [sender_target, ...]},
          "by_sender":    {sender_target: [recipient_name, ...]},
        }
    """
    by_recipient: dict[str, list[dict]] = {}
    by_sender: dict[str, list[dict]] = {}
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(seconds=ACTIVE_DISPATCH_MAX_AGE_SEC)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for state_dir, _team in _all_state_dirs():
        db_path = state_dir / "tasks.db"
        if not db_path.exists():
            continue
        rows = _task_store(state_dir).list(status="running", since=cutoff, limit=1000)
        for d in rows:
            dispatched_at = d.get("dispatched_at")
            recipient = d.get("worker") or "?"
            sender = d.get("dispatched_by") or ""
            sender_label = sender or "?"
            inc_list = by_recipient.setdefault(recipient, [])
            if not any(e["label"] == sender_label for e in inc_list):
                inc_list.append({"label": sender_label, "started_at": dispatched_at})
            if sender:
                out_list = by_sender.setdefault(sender, [])
                if not any(e["label"] == recipient for e in out_list):
                    out_list.append({"label": recipient, "started_at": dispatched_at})
    return {"by_recipient": by_recipient, "by_sender": by_sender}


MAILBOX_RECENT_WINDOW_SEC = 300  # 'recent' activity window for drained mailbox messages


def _mailbox_index() -> dict:
    """Summarize mailbox activity for each worker across all team dirs.

    Reads two sources per worker:
      - <worker>.jsonl                pending messages (count + senders)
      - <worker>.drained.jsonl        history; counts only messages within
                                       MAILBOX_RECENT_WINDOW_SEC.

    Workers auto-drain mailboxes on MAILBOX_NOTIFY trigger, so pending is
    usually 0. The 'recent' bucket surfaces just-drained activity so the
    dashboard still shows that something happened.

    Returns:
        {
          worker_name: {
            "count": <pending count>,
            "senders": [<unique pending senders>],
            "recent_count": <drained-in-last-N-seconds>,
            "recent_senders": [<unique recent senders>],
          }
        }
    Workers with both counts at 0 are omitted.
    """
    out: dict[str, dict] = {}
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=MAILBOX_RECENT_WINDOW_SEC)

    def entry_for(worker: str) -> dict:
        return out.setdefault(worker, {"count": 0, "senders": [], "recent_count": 0, "recent_senders": []})

    for state_dir, _team in _all_state_dirs():
        mb_dir = state_dir / "mailboxes"
        if not mb_dir.is_dir():
            continue
        for f in mb_dir.glob("*.jsonl"):
            is_drained = f.name.endswith(".drained.jsonl")
            worker = f.name[: -len(".drained.jsonl")] if is_drained else f.stem
            try:
                raw = f.read_text().strip()
            except Exception:
                continue
            if not raw:
                continue
            kept_senders: list[str] = []
            kept_count = 0
            for line in raw.splitlines():
                try:
                    msg = json.loads(line)
                except Exception:
                    continue
                if is_drained:
                    created_at = msg.get("created_at")
                    if not created_at:
                        continue
                    try:
                        ts = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                    except Exception:
                        continue
                    if ts < cutoff:
                        continue
                kept_count += 1
                sender = msg.get("from_worker") or "?"
                if sender not in kept_senders:
                    kept_senders.append(sender)
            if kept_count == 0:
                continue
            d = entry_for(worker)
            if is_drained:
                d["recent_count"] += kept_count
                for s in kept_senders:
                    if s not in d["recent_senders"]:
                        d["recent_senders"].append(s)
            else:
                d["count"] += kept_count
                for s in kept_senders:
                    if s not in d["senders"]:
                        d["senders"].append(s)
    return {k: v for k, v in out.items() if v["count"] or v["recent_count"]}


def _registered_workers_by_team() -> dict:
    """{worker_name: team_name} for every registered worker across all team dirs.

    Lets the hub know which team each spawn-worker/invite-worker entry belongs
    to — including sibling worktree teams that don't run their own hub.
    """
    import re as _re

    result: dict[str, str] = {}
    for state_dir, team_name in _all_state_dirs():
        reg_file = state_dir / "workers-runtime.env"
        if not reg_file.exists():
            continue
        try:
            text = reg_file.read_text(errors="replace")
        except OSError:
            continue
        for m in _re.finditer(r"^WORKER_\w+_NAME=(.+)$", text, _re.M):
            val = m.group(1).strip().strip("'").strip('"')
            if val:
                result[val] = team_name
    return result


def _registered_teams_by_target() -> dict:
    """{registry TARGET (session:window) -> team_name} across the hub's team dirs.

    Target-keyed, unlike `_registered_workers_by_team` (name-keyed). A worker
    NAME duplicated across teams — e.g. a stale leftover entry in one team plus
    the live entry in another — no longer collides on team assignment, because a
    pane only matches the registry entry that lists ITS actual target.
    """
    import re as _re

    result: dict[str, str] = {}
    for state_dir, team_name in _all_state_dirs():
        reg_file = state_dir / "workers-runtime.env"
        if not reg_file.exists():
            continue
        try:
            text = reg_file.read_text(errors="replace")
        except OSError:
            continue
        # WINDOW_ID as well as TARGET: a renamed window still resolves to its
        # worker via the id, and team assignment has to follow or the pane
        # keeps its name but loses its group.
        for m in _re.finditer(r"^WORKER_\w+_(?:TARGET|WINDOW_ID)=(.+)$", text, _re.M):
            val = m.group(1).strip().strip("'").strip('"')
            if val:
                result[val] = team_name
    return result


def _registered_team_for_worker(worker: dict, teams_by_target: dict) -> str | None:
    """Team for a scanned worker, matched by tmux TARGET (not name).

    The registry stores `session:window-name`; a scanned pane reports
    `session:window-index.pane-index`. Try both shapes (and the worker's
    window_name) so an invited/spawned worker resolves to the team whose
    registry lists its target — and a same-name entry elsewhere does not."""
    target = worker.get("target", "")
    if not target:
        return None
    session, _, win = target.partition(":")
    # Window id first — it is the one key a rename can't invalidate.
    candidates = [worker["window_id"]] if worker.get("window_id") else []
    candidates.append(target)
    if win and "." in win:
        candidates.append(f"{session}:{win.rsplit('.', 1)[0]}")  # drop .pane
    window_name = worker.get("window_name")
    if window_name and session:
        candidates.append(f"{session}:{window_name}")
    for c in candidates:
        if c in teams_by_target:
            return teams_by_target[c]
    return None


def _orchestrator_team_index(registered_by_team: dict | None = None) -> dict:
    """Scan task history across all team dirs and tag dispatching panes.

    Returns: {sender_session_window: team_name}

    Team resolution per task:
      - if recipient is a registered worker → that worker's team (from the
        registry of whichever team dir the worker lives in)
      - otherwise → recipient's tmux session

    Two signals feed this, and they are kept apart so the result doesn't depend
    on directory iteration order: `orchestrator.txt` is an explicit marker the
    spawner wrote, dispatch history is inferred, and explicit wins.
    """
    if registered_by_team is None:
        registered_by_team = _registered_workers_by_team()
    from_marker: dict[str, str] = {}
    from_dispatch: dict[str, str] = {}
    state_dirs = _all_state_dirs()
    known_teams = _team_names(state_dirs)
    for state_dir, team_name in state_dirs:
        # Explicit marker: spawn-worker.sh / invite-worker.sh record the
        # caller pane here. Lets us identify the orchestrator without
        # needing any historical dispatch.
        marker = state_dir / "orchestrator.txt"
        if marker.exists():
            try:
                pane = marker.read_text().strip()
            except Exception:
                pane = ""
            if pane:
                from_marker[pane] = team_name

        db_path = state_dir / "tasks.db"
        if not db_path.exists():
            continue
        for d in _task_store(state_dir).dispatch_pairs():
            sender = d.get("dispatched_by")
            target = d.get("tmux_target") or ""
            recipient = d.get("worker")
            if not sender or ":" not in target:
                continue
            # Prefer the task's tmux_target session when it matches a known
            # team (default / default-sub_a / …). The registry-based
            # fallback misattributes a pane to the wrong team when a sibling
            # worktree dir holds a stale entry for the same recipient name
            # (last-wins iteration order in registered_by_team).
            target_session = target.split(":", 1)[0]
            known_team = _team_for_session(target_session, known_teams)
            if known_team:
                team_sn = known_team
            elif recipient in registered_by_team:
                team_sn = registered_by_team[recipient]
            else:
                team_sn = target_session
            sender_sn = sender.split(":", 1)[0] if ":" in sender else ""
            if not sender_sn or sender_sn == team_sn:
                continue
            # dispatch_pairs() yields newest first, so the first team we see for
            # a sender is where it most recently dispatched. An orchestrator
            # that moved on to another team is tagged by where it works now.
            from_dispatch.setdefault(sender, team_sn)
    return {**from_dispatch, **from_marker}


def _worker_session_window(target: str) -> str:
    """Return the pane-level identifier for orchestrator/dispatch matching.

    Used as the key into _orchestrator_team_index and _active_dispatches_index's
    by_sender bucket. dispatch.sh now records dispatched_by with pane index
    (e.g. '0:1.1'), so we keep the same shape — full target including pane —
    so split panes within the same window are distinguished as separate
    orchestrators.
    """
    return target or ""


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a, **kw):
        pass

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self._send_json({"service": "darkarchon-hub", "ui": "http://localhost:5173"})
        elif self.path == "/api/status":
            self._send_json(self._status())
        elif self.path.startswith("/api/task/"):
            tid = self.path[len("/api/task/") :].split("?")[0]
            self._send_json(self._task(tid))
        elif self.path == "/api/hosts":
            self._send_json({"hosts": STORE.get_hosts()})
        elif self.path == "/api/teams":
            # Read-only. Archiving is deliberately left to lib/teams.sh so that
            # nothing reachable over the network can move a team's history.
            self._send_json({"teams": _teams_payload(set()),
                             "dormant_days": TEAM_DORMANT_DAYS,
                             "stale_days": TEAM_STALE_DAYS})
        elif self.path == "/api/events":
            self._stream_events()
        else:
            self._send(404, "text/plain", b"not found")

    def do_POST(self):
        if self.path.startswith("/api/hosts/") and self.path.endswith("/state"):
            host_id = self.path[len("/api/hosts/") : -len("/state")]
            length = int(self.headers.get("Content-Length", "0"))
            if length == 0 or length > 1_000_000:
                self._send(400, "text/plain", b"bad request")
                return
            try:
                body = json.loads(self.rfile.read(length))
            except json.JSONDecodeError:
                self._send(400, "text/plain", b"invalid json")
                return
            workers = body.get("workers", [])
            if not isinstance(workers, list):
                self._send(400, "text/plain", b"workers must be a list")
                return
            teams = body.get("teams") or []
            if not isinstance(teams, list):
                self._send(400, "text/plain", b"teams must be a list")
                return
            events = list(STORE.update_host(host_id, workers, teams))
            for ev in events:
                BROKER.publish(ev)
            self._send_json({"accepted": True, "events": len(events)})
        elif self.path == "/api/ack":
            # Mark finished-but-unreviewed work as seen. Body: {"all": true}
            # or {"host": ..., "target": ...}. The UI calls this when the user
            # opens a worker's detail panel or hits the clear-all button;
            # tmux-side viewing is acked hub-side via the `focused` flag.
            length = int(self.headers.get("Content-Length", "0"))
            if length > 10_000:
                self._send(400, "text/plain", b"bad request")
                return
            try:
                body = json.loads(self.rfile.read(length)) if length else {}
            except json.JSONDecodeError:
                self._send(400, "text/plain", b"invalid json")
                return
            if not isinstance(body, dict):
                self._send(400, "text/plain", b"body must be an object")
                return
            if body.get("all"):
                acked = STORE.ack()
            else:
                host, target = body.get("host"), body.get("target")
                if not host or not target:
                    self._send(400, "text/plain", b"host and target required")
                    return
                acked = STORE.ack(host, target)
            self._send_json({"acked": acked})
        else:
            self._send(404, "text/plain", b"not found")

    def _stream_events(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        sub = BROKER.subscribe()
        try:
            self.wfile.write(b": ok\n\n")
            self.wfile.flush()
            while True:
                try:
                    ev = sub.get(timeout=15)
                    self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
                    self.wfile.flush()
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            BROKER.unsubscribe(sub)

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, obj):
        self._send(200, "application/json; charset=utf-8", json.dumps(obj).encode())

    def _status(self):
        workers = STORE.get_all_workers()
        dispatches = _active_dispatches_index()
        registered_by_team = _registered_workers_by_team()
        registered_by_target = _registered_teams_by_target()
        orchestrator_team = _orchestrator_team_index(registered_by_team)
        mailbox_idx = _mailbox_index()
        for w in workers:
            w["pending_mailbox"] = mailbox_idx.get(w["name"])
            w["recent_tasks"] = list_recent_tasks(w["name"])
            w["last_activity_age"] = humanize_age(last_activity(w["name"]))
            # recent_output is now pushed by each host's agent (via
            # scan_panes) and stored verbatim by HostStateStore. The hub
            # used to call capture_pane locally as a fallback — dropped
            # since cross-host workers couldn't be reached that way.
            sender_key = _worker_session_window(w.get("target", ""))
            incoming = dispatches["by_recipient"].get(w["name"], [])
            outgoing = dispatches["by_sender"].get(sender_key, []) if sender_key else []
            w["incoming_dispatches"] = incoming
            w["outgoing_dispatches"] = outgoing

            # Team membership precedence:
            #   1. registered worker (workers-runtime.env) — always this hub's team,
            #      regardless of which tmux session the pane happens to live in. This
            #      covers invited EXTERNAL workers (e.g. user-owned pane in another
            #      session) added via invite-worker.sh.
            #   2. orchestrator — pane has historically dispatched into another team's
            #      session. Pulled into that team.
            #   3. default — worker's own session name.
            default_team = sender_key.split(":", 1)[0] if sender_key else ""
            forced_team = orchestrator_team.get(sender_key) if sender_key else None
            # Priority order for team_name:
            #   1. tmux session matches a known team (default /
            #      default-sub_a etc) — most reliable, handles same-name
            #      workers spawned in multiple worktrees.
            #   2. orchestrator pane that dispatched into a known team.
            #   3. registered worker whose session is none of ours (invited
            #      EXTERNAL like writer at docu:1) — fall back to registry.
            #      Matched by TARGET, so a stale/foreign same-NAME entry can't
            #      hijack the team (see _registered_team_for_worker).
            #   4. default: own session.
            hub_team_by_session = _team_for_session(default_team)
            registered_team = _registered_team_for_worker(w, registered_by_target)
            if hub_team_by_session:
                w["team_name"] = hub_team_by_session
                w["is_orchestrator"] = False
            elif forced_team:
                w["team_name"] = forced_team
                w["is_orchestrator"] = True
            elif registered_team and w.get("kind") == "registered":
                w["team_name"] = registered_team
                w["is_orchestrator"] = False
            else:
                w["team_name"] = default_team
                w["is_orchestrator"] = False
        workers.sort(key=lambda w: (w.get("external", False), w.get("host", ""), w.get("name", "")))
        return {
            "session_name": SESSION_NAME,
            "state_dir": str(STATE_DIR),
            "hosts": STORE.get_hosts(),
            "workers": workers,
            "teams": _teams_payload(_live_team_keys(workers)),
            "ts": datetime.now(timezone.utc).isoformat(),
        }

    def _task(self, tid):
        row = _task_store().get(tid)
        return row or {"error": "not found", "id": tid}


def main():
    global STATE_DIR, STATE_ROOT, SESSION_NAME, STORE
    global TEAM_DORMANT_DAYS, TEAM_STALE_DAYS

    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--session-name", default=SESSION_NAME)
    p.add_argument("--state-dir", default=str(STATE_DIR))
    p.add_argument(
        "--state-root",
        help="parent of the team state dirs (default: parent of --state-dir)",
    )
    p.add_argument("--stale-after", type=float, default=30.0)
    p.add_argument("--evict-after", type=float, default=300.0)
    p.add_argument("--dormant-days", type=int, default=TEAM_DORMANT_DAYS,
                   help="team idle this long is 'dormant' (config.env TEAM_DORMANT_DAYS)")
    p.add_argument("--stale-days", type=int, default=TEAM_STALE_DAYS,
                   help="team idle this long is 'stale' (config.env TEAM_STALE_DAYS)")
    args = p.parse_args()

    SESSION_NAME = args.session_name
    STATE_DIR = Path(args.state_dir)
    STATE_ROOT = Path(args.state_root) if args.state_root else STATE_DIR.parent
    TEAM_DORMANT_DAYS = args.dormant_days
    TEAM_STALE_DAYS = args.stale_days
    STORE = HostStateStore(
        stale_after_seconds=args.stale_after,
        evict_after_seconds=args.evict_after,
    )

    print(f"Team hub [{SESSION_NAME}] → http://{args.host}:{args.port}")
    print(f"State dir:        {STATE_DIR}")
    print(f"State root:       {STATE_ROOT} ({len(_all_state_dirs())} teams)")
    print(f"Stale-after:      {args.stale_after}s")
    print(f"Evict-after:      {args.evict_after}s")
    print("Ctrl-C to stop.")

    class ReusableThreadingServer(http.server.ThreadingHTTPServer):
        allow_reuse_address = True
        daemon_threads = True  # don't block process exit if SSE clients hang

    try:
        httpd = ReusableThreadingServer((args.host, args.port), Handler)
        threading.Thread(target=_question_watcher_loop, daemon=True).start()
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")


if __name__ == "__main__":
    main()
