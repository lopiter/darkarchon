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


def _team_for_session(session: str) -> str | None:
    """Return the team name whose SESSION_NAME equals `session`, or None.

    This is the most accurate signal of team membership — a worker's tmux
    session directly tells us which hub team it belongs to. Used to break
    ties when the same worker name exists in multiple worktree registries.
    """
    if not session:
        return None
    for _state_dir, team_name in _all_state_dirs():
        if session == team_name:
            return team_name
    return None


def _all_state_dirs() -> list:
    """Discover hub's own + worktree sibling state directories.

    Layout:
      ~/.darkarchon/<team>/                <-- hub's STATE_DIR (own)
      ~/.darkarchon/<team>/<sub_a>/        <-- worktree (sub-dir)
      ~/.darkarchon/<team>/<sub_b>/        <-- worktree

    A sub-dir is treated as a worktree state dir only when it contains
    workers-runtime.env. Its team name is derived as SESSION_NAME +
    '-' + sub_dir_name to match the worktree's own SESSION_NAME.

    Returns: list of (state_dir_path, team_name).
    """
    out = [(STATE_DIR, SESSION_NAME)]
    if STATE_DIR.is_dir():
        for sub in sorted(STATE_DIR.iterdir()):
            if not sub.is_dir():
                continue
            if not (sub / "workers-runtime.env").exists():
                continue
            team_name = f"{SESSION_NAME}-{sub.name}"
            out.append((sub, team_name))
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
            text = reg_file.read_text()
        except Exception:
            continue
        for m in _re.finditer(r"^WORKER_\w+_NAME=(.+)$", text, _re.M):
            val = m.group(1).strip().strip("'").strip('"')
            if val:
                result[val] = team_name
    return result


def _orchestrator_team_index(registered_by_team: dict | None = None) -> dict:
    """Scan task history across all team dirs and tag dispatching panes.

    Returns: {sender_session_window: team_name}

    Team resolution per task:
      - if recipient is a registered worker → that worker's team (from the
        registry of whichever team dir the worker lives in)
      - otherwise → recipient's tmux session
    """
    if registered_by_team is None:
        registered_by_team = _registered_workers_by_team()
    pane_to_team: dict[str, str] = {}
    for state_dir, team_name in _all_state_dirs():
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
                pane_to_team[pane] = team_name

        db_path = state_dir / "tasks.db"
        if not db_path.exists():
            continue
        for d in _task_store(state_dir).list(limit=10000):
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
            known_team = _team_for_session(target_session)
            if known_team:
                team_sn = known_team
            elif recipient in registered_by_team:
                team_sn = registered_by_team[recipient]
            else:
                team_sn = target_session
            sender_sn = sender.split(":", 1)[0] if ":" in sender else ""
            if not sender_sn or sender_sn == team_sn:
                continue
            pane_to_team[sender] = team_sn
    return pane_to_team


def _worker_session_window(target: str) -> str:
    """Return the pane-level identifier for orchestrator/dispatch matching.

    Used as the key into _orchestrator_team_index and _active_dispatches_index's
    by_sender bucket. dispatch.sh now records dispatched_by with pane index
    (e.g. '0:1.1'), so we keep the same shape — full target including pane —
    so split panes within the same window are distinguished as separate
    orchestrators.
    """
    return target or ""


HTML = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><title>Team Dashboard</title>
<style>
  body { font-family: -apple-system, system-ui, sans-serif; background: #f0f9e8; margin: 0; padding: 20px; }
  .host-group { margin-bottom: 28px; }
  .host-header { font-size: 1.1rem; font-weight: 800; color: #4a7a1c; margin: 0 0 10px 4px; }
  .host-header .h-host { letter-spacing: 0.04em; }
  .host-header .h-count { font-size: 0.75rem; color: #8aa663; font-weight: 600; margin-left: 8px; }
  .session-group { background: rgba(255,255,255,0.5); border-left: 3px solid #b5d678; border-radius: 0 12px 12px 0; padding: 10px 12px 14px; margin-bottom: 12px; }
  .session-header { font-size: 0.78rem; font-weight: 700; color: #7db53a; text-transform: uppercase; letter-spacing: 0.08em; margin: 0 0 10px 0; }
  .session-header .s-count { color: #aac; font-weight: 600; margin-left: 6px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 12px; }
  .panel { background: white; border-radius: 12px; padding: 14px; box-shadow: 0 2px 4px rgba(0,0,0,0.06); }
  .name { font-weight: 700; font-size: 1.0rem; }
  .role { font-size: 0.7rem; color: #999; margin-top: 4px; }
  .state-badge { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 0.7rem; font-weight: 700; color: white; }
  .state-badge.busy { background: #ff7e67; }
  .state-badge.idle { background: #81d4fa; }
  .state-badge.typed { background: #ffca28; color: #333; }
  .state-badge.compacting { background: #b39ddb; }
  .state-badge.rate_limited { background: #ef5350; }
  .state-badge.dead { background: #9e9e9e; }
  .state-badge.unknown { background: #ccc; color: #333; }
  .detail { font-size: 0.75rem; color: #666; font-family: ui-monospace, monospace; margin-top: 6px; word-break: break-all; }
  .meta { font-size: 0.7rem; color: #999; margin-top: 8px; }
  .dispatches { font-size: 0.75rem; color: #4a7a1c; margin-top: 6px; font-family: ui-monospace, monospace; line-height: 1.4; }
  .dispatches .arrow { color: #aaa; }
  .mailbox { font-size: 0.75rem; color: #b88300; margin-top: 4px; font-family: ui-monospace, monospace; line-height: 1.4; }
  .host-header.stale { color: #c46b3a; }
  .empty-host { font-size: 0.78rem; color: #999; font-style: italic; padding: 6px 10px; background: rgba(255,255,255,0.5); border-left: 3px solid #d0d0d0; border-radius: 0 12px 12px 0; }
</style></head><body>
<h1>Team — <span id="session">…</span></h1>
<div id="counts">—</div>
<div id="root"></div>
<script>
function esc(s) { return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

function teamOf(w) {
  // team_name set by hub: either the worker's own session, or the team it
  // dispatches into (orchestrator). Fallback to session derived from target.
  if (w.team_name) return w.team_name;
  const tgt = w.target || '';
  const idx = tgt.indexOf(':');
  return idx > 0 ? tgt.slice(0, idx) : '(unknown)';
}

function renderCard(w) {
  const elapsedSuffix = (entry) => {
    if (typeof entry !== 'object' || !entry || !entry.started_at) return '';
    const t = new Date(entry.started_at).getTime();
    if (isNaN(t)) return '';
    const s = Math.max(0, Math.round((Date.now() - t) / 1000));
    return ` (${s}s)`;
  };
  const labelOf = (e) => typeof e === 'string' ? e : (e && e.label) || '';
  const inc = (w.incoming_dispatches || []).map(e => `<span class="arrow">←</span> ${esc(labelOf(e))}${elapsedSuffix(e)}`);
  const out = (w.outgoing_dispatches || []).map(e => `<span class="arrow">→</span> ${esc(labelOf(e))}${elapsedSuffix(e)}`);
  const dispatches = [...out, ...inc].join('<br>');
  const mb = w.pending_mailbox;
  const mbParts = [];
  if (mb && mb.count) {
    mbParts.push(`📩 ← ${esc(mb.senders.join(', '))} (${mb.count} pending)`);
  }
  if (mb && mb.recent_count) {
    mbParts.push(`📥 ← ${esc(mb.recent_senders.join(', '))} (${mb.recent_count} in 5m)`);
  }
  const mbLine = mbParts.join('<br>');
  const orchestratorTag = w.is_orchestrator ? ' [orchestrator]' : '';
  return `<div class="panel">
    <div class="name">${esc(w.name)}${orchestratorTag}</div>
    <div class="role">${esc(w.role || w.process || '—')} · <span class="state-badge ${w.state}">${esc(w.state)}</span></div>
    ${w.detail ? `<div class="detail">${esc(w.detail)}</div>` : ''}
    ${dispatches ? `<div class="dispatches">${dispatches}</div>` : ''}
    ${mbLine ? `<div class="mailbox">${mbLine}</div>` : ''}
    <div class="meta">${esc(w.target || '')}</div>
  </div>`;
}

function humanizeAge(ts) {
  if (!ts) return '';
  const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s/60)}m ago`;
  return `${Math.floor(s/3600)}h ago`;
}

async function refresh() {
  try {
    const r = await fetch('/api/status', {cache:'no-store'});
    const d = await r.json();
    document.getElementById('session').textContent = d.session_name || '—';
    const root = document.getElementById('root');
    const counts = {};

    // host → team → worker[]   (team is team_name from hub: session for plain
    // workers, dispatch-target session for orchestrators)
    const byHost = {};
    const hostMeta = {};

    // Seed byHost with every connected host so agent-up-but-no-workers
    // hosts still render (otherwise they'd be invisible).
    (d.hosts || []).forEach(h => {
      hostMeta[h.host_id] = h;
      byHost[h.host_id] = byHost[h.host_id] || {};
    });

    d.workers.forEach(w => {
      counts[w.state] = (counts[w.state] || 0) + 1;
      const tn = teamOf(w);
      byHost[w.host] = byHost[w.host] || {};
      byHost[w.host][tn] = byHost[w.host][tn] || [];
      byHost[w.host][tn].push(w);
    });

    const hostNames = Object.keys(byHost).sort();
    root.innerHTML = hostNames.map(host => {
      const sessions = byHost[host];
      const hostTotal = Object.values(sessions).reduce((a, ws) => a + ws.length, 0);
      const meta = hostMeta[host];
      const ageStr = meta ? humanizeAge(meta.last_seen) : '';
      const staleClass = meta && meta.stale ? ' stale' : '';
      const staleLabel = meta && meta.stale ? 'stale' : 'connected';

      if (hostTotal === 0) {
        const info = ageStr ? `agent ${staleLabel} · ${ageStr}` : `agent ${staleLabel}`;
        return `<section class="host-group">
          <h2 class="host-header${staleClass}">🖥 <span class="h-host">${esc(host)}</span><span class="h-count">${esc(info)}</span></h2>
          <div class="empty-host">no workers detected</div>
        </section>`;
      }

      const sessionNames = Object.keys(sessions).sort();
      const sessionBlocks = sessionNames.map(sn => {
        const ws = sessions[sn];
        const cards = ws.map(renderCard).join('');
        return `<div class="session-group">
          <h3 class="session-header"><span class="s-name">${esc(sn)}</span><span class="s-count">${ws.length}</span></h3>
          <div class="grid">${cards}</div>
        </div>`;
      }).join('');
      const countLabel = ageStr ? `${hostTotal} workers · ${ageStr}` : `${hostTotal} workers`;
      return `<section class="host-group">
        <h2 class="host-header${staleClass}">🖥 <span class="h-host">${esc(host)}</span><span class="h-count">${esc(countLabel)}</span></h2>
        ${sessionBlocks}
      </section>`;
    }).join('');

    document.getElementById('counts').textContent = `${d.workers.length} workers · ` + Object.entries(counts).map(([k,v]) => `${v} ${k}`).join(' · ');
  } catch (e) {
    document.getElementById('counts').textContent = 'error: ' + e.message;
  }
}

function ensureNotificationPermission() {
  if (!('Notification' in window)) return;
  if (Notification.permission === 'default') Notification.requestPermission();
}

function notify(title, body) {
  if (!('Notification' in window) || Notification.permission !== 'granted') return;
  new Notification(title, { body });
}

function startEventStream() {
  const src = new EventSource('/api/events');
  src.onmessage = (e) => {
    try {
      const ev = JSON.parse(e.data);
      if (ev.type === 'state_change' && ev.from === 'busy' && ev.to === 'typed') {
        // Worker was running and paused for a user decision (Claude
        // permission prompt mid-task, etc.). Skip idle→typed which is
        // usually just the user starting to type into an empty prompt.
        notify(`🙋 ${ev.worker.name} waiting for input`, `${ev.host} · needs your decision`);
      } else if (ev.type === 'new_question') {
        notify(`❓ Question from ${ev.from_worker}`, ev.body);
      }
      // 'busy → idle' (task completed) is intentionally NOT alerted —
      // user only wants notifications that demand their input/decision.
    } catch {}
  };
  src.onerror = () => {
    src.close();
    setTimeout(startEventStream, 5000);
  };
}

ensureNotificationPermission();
refresh();
setInterval(refresh, 3000);
startEventStream();
</script></body></html>
"""


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a, **kw):
        pass

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self._send(200, "text/html; charset=utf-8", HTML.encode())
        elif self.path == "/api/status":
            self._send_json(self._status())
        elif self.path.startswith("/api/task/"):
            tid = self.path[len("/api/task/") :].split("?")[0]
            self._send_json(self._task(tid))
        elif self.path == "/api/hosts":
            self._send_json({"hosts": STORE.get_hosts()})
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
            events = list(STORE.update_host(host_id, workers))
            for ev in events:
                BROKER.publish(ev)
            self._send_json({"accepted": True, "events": len(events)})
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
            #   4. default: own session.
            hub_team_by_session = _team_for_session(default_team)
            registered_team = registered_by_team.get(w["name"])
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
            "ts": datetime.now(timezone.utc).isoformat(),
        }

    def _task(self, tid):
        row = _task_store().get(tid)
        return row or {"error": "not found", "id": tid}


def main():
    global STATE_DIR, SESSION_NAME, STORE

    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--session-name", default=SESSION_NAME)
    p.add_argument("--state-dir", default=str(STATE_DIR))
    p.add_argument("--stale-after", type=float, default=30.0)
    args = p.parse_args()

    SESSION_NAME = args.session_name
    STATE_DIR = Path(args.state_dir)
    STORE = HostStateStore(stale_after_seconds=args.stale_after)

    print(f"Team hub [{SESSION_NAME}] → http://{args.host}:{args.port}")
    print(f"State dir:        {STATE_DIR}")
    print(f"Stale-after:      {args.stale_after}s")
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
