"""Core logic for the darkarchon-orchestrators Hermes plugin.

Hermes acts as a *fleet manager*: it spawns Claude Code "orchestrator"
sessions in tmux (via darkarchon's spawn-worker.sh, role=orchestrator) and
dispatches tasks to them with darkarchon's file-based protocol
(prompt file → tmux trigger → result file). Each orchestrator in turn runs
its own darkarchon worker team, namespaced by DARKARCHON_TEAM=<its name>.

Everything here shells out to the battle-tested darkarchon scripts — this
module adds only fleet-level bookkeeping:

- manager namespace: all fleet-level darkarchon calls run with
  DARKARCHON_TEAM=$HERMES_ORCH_TEAM (default "hermes"), so orchestrator
  windows live in tmux session "hermes" and registry/state in
  ~/.darkarchon/hermes/.
- dispatch runs: dispatch-safe.sh blocks until the orchestrator finishes,
  which can be minutes to hours — far beyond a sane foreground tool budget.
  So dispatch() launches it as a detached subprocess, records a run under
  <state_dir>/hermes-runs/<run_id>.json + .log, and result() polls it.

Stdlib only; no hermes imports at module level (keeps this testable outside
the hermes venv).
"""

from __future__ import annotations

import json
import os
import re
import secrets
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

# PluginContext handed over by __init__.register(). Enables completion push:
# a watcher thread injects a message into the hermes conversation when a
# backgrounded dispatch finishes, so nobody has to poll. None outside hermes
# (tests, direct use) — notification silently degrades to just finalizing.
_CTX = None


def set_context(ctx) -> None:
    global _CTX
    _CTX = ctx
    _ensure_question_watcher()

# In-memory handles for dispatches started by THIS hermes process. Survives
# across tool calls (module lives as long as the process); after a hermes
# restart result() falls back to pid-liveness + the run's log file.
_PROCS: Dict[str, subprocess.Popen] = {}

RESULT_TRUNCATE_CHARS = 8000
LOG_TAIL_CHARS = 2000
DISPATCH_WAIT_CAP_SECONDS = 540  # stay under hermes' 600s foreground tool cap

# Fleet-level dispatch hard cap. Must exceed the orchestrator's own
# worker-dispatch cap (darkarchon default 3600s) — both clocks run
# concurrently and the fleet clock starts first, so equal budgets would
# time the manager out while the orchestrator's worker task still runs.
FLEET_TASK_MAX_SECONDS = "7200"


def darkarchon_home() -> Path:
    return Path(os.environ.get("DARKARCHON_HOME", "~/work/darkarchon")).expanduser()


def _team_state_file() -> Path:
    return (Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()
            / "darkarchon-orchestrators.json")


def manager_team() -> Optional[str]:
    """Fleet team name = tmux session for orchestrators + state dir key.

    Resolution: HERMES_ORCH_TEAM env (explicit override) > the name the user
    chose at first use (persisted by set_fleet). No default — the user names
    their fleet; callers get an actionable error until then.
    """
    env = os.environ.get("HERMES_ORCH_TEAM")
    if env:
        return env
    try:
        team = json.loads(_team_state_file().read_text()).get("team")
        return team if team else None
    except (OSError, json.JSONDecodeError):
        return None


_NO_TEAM = (
    "No fleet name is set yet. STOP — do NOT call set_fleet in this turn, and "
    "do NOT make up a name. End your turn by ASKING THE USER what to name the "
    "fleet (it namespaces state under ~/.darkarchon/<name>/). Call "
    "action=set_fleet only in a later turn, with the name the user typed."
)


def set_fleet(team: str, force: bool = False) -> Dict[str, Any]:
    team = (team or "").strip()
    if not team or not NAME_RE.match(team):
        return _err(f"invalid team name '{team}' (use [a-zA-Z0-9_-])")
    current = manager_team()
    if current and current != team and not force:
        # Switching the fleet namespace hides every currently-registered
        # employee from list/dispatch (they keep running in tmux, invisibly).
        # Historically "hire X into team Y" got misread as set_team(Y) and
        # orphaned whole rosters — refuse and steer to the group label.
        names = _registry_names()
        if names:
            return _err(
                f"fleet '{current}' still has {len(names)} employee(s) "
                f"registered: {', '.join(names)}. Switching the fleet to "
                f"'{team}' would make them invisible to list/dispatch (they "
                f"keep running in tmux). If the user meant to hire into a "
                f"TEAM/GROUP named '{team}', that is NOT this action — use "
                f"spawn with team='{team}' (a label inside the current "
                f"fleet). Only pass force=true if the user explicitly wants "
                f"to abandon the current fleet."
            )
    f = _team_state_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"team": team}))
    return {"ok": True, "team": team,
            "note": f"Fleet name set. Registry and state live in "
                    f"~/.darkarchon/{team}/; each employee gets its own tmux "
                    f"session named after it."}


# Deprecated alias — "team" used to mean the fleet namespace, which collided
# with employee team labels (spawn's team=). Kept for old callers/tests.
set_team = set_fleet


def state_dir() -> Path:
    prefix = os.environ.get("TOOL_PREFIX", "darkarchon")
    return Path.home() / f".{prefix}" / str(manager_team())


def runs_dir() -> Path:
    d = state_dir() / "hermes-runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── employee groups ("X팀" labels inside ONE fleet namespace) ──────────────
# The fleet namespace (manager_team) is global and singular; user-visible
# "teams" of employees are just labels on registry entries. Plugin-owned
# file — darkarchon core knows nothing about it.

def _groups_file() -> Path:
    return state_dir() / "employee-groups.json"


def _load_groups() -> Dict[str, str]:
    try:
        d = json.loads(_groups_file().read_text())
        return {str(k): str(v) for k, v in d.items()} if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _set_group(name: str, group: str) -> None:
    g = _load_groups()
    if group:
        g[name] = group
    else:
        g.pop(name, None)
    f = _groups_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(g, ensure_ascii=False, indent=1))
    tmp.replace(f)


def _env(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Env for fleet-level darkarchon calls: manager team namespace."""
    env = dict(os.environ)
    env["DARKARCHON_TEAM"] = str(manager_team())
    # Hermes itself is not a darkarchon worker — make sure no inherited worker
    # identity can shadow the manager namespace in the python resolvers.
    env.pop("EE_STATE_DIR", None)
    env.pop("STATE_DIR", None)
    if extra:
        env.update(extra)
    return env


def _run(cmd: List[str], timeout: int = 60,
         extra_env: Optional[Dict[str, str]] = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout,
        env=_env(extra_env), cwd=str(darkarchon_home()),
    )


def _err(message: str, **extra: Any) -> Dict[str, Any]:
    out = {"ok": False, "error": message}
    out.update(extra)
    return out


# ── spawn / kill ───────────────────────────────────────────────────────────

def _safe_name(name: str) -> str:
    """Same scheme as safe_name() in lib/_lib.sh / worker_state.py."""
    return "".join(c if c.isalnum() or c == "_" else "_" for c in name)


def _recorded_session_id(name: str) -> str:
    """Last Claude session id the state hook recorded for this employee."""
    f = state_dir() / "states" / f"{_safe_name(name)}.json"
    try:
        return str(json.loads(f.read_text()).get("session_id") or "")
    except (OSError, json.JSONDecodeError):
        return ""


SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9-]+$")


def _latest_session_for_cwd(cwd: str) -> str:
    """The id of the most recent Claude Code session started in `cwd`, or ''.

    Claude stores sessions at ~/.claude/projects/<slug>/<id>.jsonl where the
    slug is the cwd with '/' (and '.') turned into '-'. We try that dir first,
    then fall back to scanning by the `cwd` field recorded inside each jsonl —
    so an unexpected slug rule can't hide a session."""
    target = str(Path(cwd).expanduser().resolve())
    projects = Path.home() / ".claude" / "projects"
    if not projects.is_dir():
        return ""

    def _newest(paths):
        paths = [p for p in paths if p.is_file()]
        if not paths:
            return ""
        return max(paths, key=lambda p: p.stat().st_mtime).stem

    for slug in (target.replace("/", "-"),
                 re.sub(r"[/.]", "-", target)):
        d = projects / slug
        if d.is_dir():
            newest = _newest(list(d.glob("*.jsonl")))
            if newest:
                return newest

    # fallback: newest-first global scan, matched on the recorded cwd
    allj = sorted(projects.glob("*/*.jsonl"),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    for p in allj[:300]:
        try:
            with open(p) as fh:
                for _ in range(5):
                    line = fh.readline()
                    if not line:
                        break
                    d = json.loads(line)
                    if isinstance(d, dict) and d.get("cwd"):
                        if str(Path(d["cwd"]).resolve()) == target:
                            return p.stem
                        break
        except (OSError, json.JSONDecodeError, ValueError):
            continue
    return ""


def spawn(name: str, cwd: str, brief: str = "", resume: bool = False,
          session_id: str = "", continue_work: bool = False,
          team: str = "") -> Dict[str, Any]:
    if manager_team() is None:
        return _err(_NO_TEAM)
    if not name or not NAME_RE.match(name):
        return _err(f"invalid orchestrator name '{name}' (use [a-zA-Z0-9_-])")
    if name == manager_team():
        return _err(f"name '{name}' collides with the manager team namespace")
    team = (team or "").strip()
    if team and not NAME_RE.match(team):
        return _err(f"invalid team label '{team}' (use [a-zA-Z0-9_-])")
    workdir = Path(cwd).expanduser()
    if not workdir.is_dir():
        return _err(f"cwd not found: {workdir}")

    resume_id = ""
    session_id = (session_id or "").strip()
    if continue_work and not session_id:
        # "just continue the existing work in this dir" — find it ourselves.
        session_id = _latest_session_for_cwd(str(workdir))
        if not session_id:
            return _err(
                f"no prior Claude session found for {workdir} — nothing to "
                f"continue. Hire fresh, or open Claude there once first."
            )
    if session_id:
        # Promote an arbitrary Claude conversation (e.g. the user's own past
        # session) into a NEW employee that continues it.
        if not SESSION_ID_RE.match(session_id):
            return _err(f"invalid session_id '{session_id}'")
        if name in _registry_names():
            return _err(
                f"'{name}' is already registered — session_id is for hiring a "
                f"NEW employee onto an existing conversation. Use resume=true "
                f"to re-hire a dead employee, or pick another name."
            )
        # Claude transcripts live under ~/.claude/projects/<cwd-slug>/. Verify
        # the id exists at all so a typo fails here instead of as a broken pane.
        hits = list((Path.home() / ".claude" / "projects").glob(
            f"*/{session_id}.jsonl"))
        if not hits:
            return _err(
                f"no Claude session '{session_id}' found on this machine. "
                f"Get the id via /status inside the session, and note the "
                f"employee's cwd must match the session's original cwd."
            )
        resume_id = session_id
    elif resume:
        resume_id = _recorded_session_id(name)
        if not resume_id:
            return _err(
                f"no recorded Claude session for '{name}' — it was never "
                f"spawned with state hooks (or predates them). Spawn fresh "
                f"instead (resume=false)."
            )
        # A rebooted/killed employee is usually still in the registry. Clear
        # the dead registration (and its leftover session + sub-team registry,
        # same as a lay-off) so the respawn isn't refused as a duplicate. A
        # LIVE employee is never touched — resume only replaces the dead.
        if name in _registry_names():
            st = status(name)
            if st.get("state") != "dead":
                return _err(
                    f"employee '{name}' is registered and {st.get('state')} — "
                    f"resume only applies to a dead one. Kill it first if you "
                    f"really want a restart."
                )
            cleared = kill(name)
            if not cleared.get("ok"):
                return _err("could not clear the dead registration",
                            detail=cleared.get("error", ""))

    # The employee will own a tmux session named after it — refuse names that
    # would splice windows into an unrelated pre-existing session.
    probe = subprocess.run(["tmux", "has-session", "-t", f"={name}"],
                           capture_output=True, text=True)
    if probe.returncode == 0 and name not in _registry_names():
        return _err(
            f"a tmux session named '{name}' already exists and is not one of "
            f"ours — pick a different employee name"
        )

    # An employee charter: layered into the orchestrator's system prompt at
    # launch (start-worker-claude.sh reads <context_dir>/<role>.md as layer 4).
    # Passed via DARKARCHON_PROMPT_DIR — the documented override that
    # config.env maps onto TEAM_CONTEXT_DIR (setting TEAM_CONTEXT_DIR directly
    # would be clobbered when _lib.sh sources config.env).
    extra_env = {}
    if brief and brief.strip():
        ctx_dir = state_dir() / "context" / name
        ctx_dir.mkdir(parents=True, exist_ok=True)
        (ctx_dir / "orchestrator.md").write_text(
            "## Your Charter (assigned by your manager at hire time)\n\n"
            + brief.strip() + "\n"
        )
        extra_env["DARKARCHON_PROMPT_DIR"] = str(ctx_dir)

    # --session <name>: each employee gets a DEDICATED tmux session named
    # after it. Its own workers (DARKARCHON_TEAM=<name>) land in that same
    # session, so one session == one employee + their whole sub-team.
    script = darkarchon_home() / "lib" / "spawn-worker.sh"
    args = [str(script), "--env", f"DARKARCHON_TEAM={name}", "--session", name]
    if resume_id:
        args += ["--resume-session", resume_id]
    args += [name, str(workdir), "orchestrator"]
    proc = _run(args, timeout=30, extra_env=extra_env)
    if proc.returncode != 0:
        return _err("spawn failed", detail=(proc.stderr or proc.stdout).strip())
    _set_group(name, team)
    return {
        "ok": True,
        "orchestrator": name,
        "team": team or None,
        "tmux_target": f"{name}:{name}",
        "tmux_session": name,
        "cwd": str(workdir),
        "resumed": bool(resume_id),
        "continued_session": session_id or None,
        "note": (
            (f"Hired onto existing Claude session {session_id} — it continues "
             f"that conversation. " if session_id else
             "Re-hired with its previous conversation restored (claude "
             "--resume). Its own sub-workers were reset — it may need to "
             "respawn them. " if resume_id else "")
            + "Claude takes ~15s to start. If a trust prompt appears in the "
            "tmux window the user must attach and hit Enter once. Check "
            "readiness with action=status before the first dispatch."
        ),
    }


def kill(name: str) -> Dict[str, Any]:
    if manager_team() is None:
        return _err(_NO_TEAM)
    if not name or not NAME_RE.match(name):
        return _err(f"invalid orchestrator name '{name}'")
    script = darkarchon_home() / "lib" / "kill-worker.sh"
    proc = _run([str(script), name], timeout=30)
    if proc.returncode != 0:
        return _err("kill failed", detail=(proc.stderr or proc.stdout).strip())
    # The employee owned a whole session (itself + its worker windows) and a
    # team registry. Tear both down so a future re-hire starts clean; task
    # history (tasks.db etc.) under ~/.darkarchon/<name>/ is kept.
    subprocess.run(["tmux", "kill-session", "-t", f"={name}"],
                   capture_output=True, text=True)
    prefix = os.environ.get("TOOL_PREFIX", "darkarchon")
    reg = Path.home() / f".{prefix}" / name / "workers-runtime.env"
    try:
        reg.unlink()
    except OSError:
        pass
    _set_group(name, "")
    return {"ok": True, "orchestrator": name,
            "message": proc.stdout.strip(),
            "note": f"tmux session '{name}' (employee + its workers) removed."}


# ── state / list ───────────────────────────────────────────────────────────

def status(name: str) -> Dict[str, Any]:
    if manager_team() is None:
        return _err(_NO_TEAM)
    if not name or not NAME_RE.match(name.lstrip("@")):
        return _err(f"invalid orchestrator name '{name}'")
    resolved, cands = _resolve_name(name)
    if resolved is None and cands:
        return _err(f"ambiguous employee '{name}' — matches: {', '.join(cands)}")
    if resolved:
        name = resolved
    script = darkarchon_home() / "lib" / "worker_state.py"
    proc = _run(["python3", str(script), name, "--json"], timeout=30)
    if proc.returncode != 0:
        return _err("state resolution failed",
                    detail=(proc.stderr or proc.stdout).strip())
    try:
        return {"ok": True, "orchestrator": name, **json.loads(proc.stdout)}
    except json.JSONDecodeError:
        return {"ok": True, "orchestrator": name, "raw": proc.stdout.strip()}


def _registry_names() -> List[str]:
    """Orchestrator names from the manager team's runtime registry."""
    reg = state_dir() / "workers-runtime.env"
    if not reg.is_file():
        return []
    names: Dict[str, str] = {}
    pat = re.compile(r"^WORKER_([A-Za-z0-9_]+)_(NAME|TARGET)=(.*)$")
    for line in reg.read_text().splitlines():
        m = pat.match(line.strip())
        if not m:
            continue
        sn, field, value = m.groups()
        # values are written with printf %q — plain names need no unquoting,
        # but strip surrounding quotes defensively.
        value = value.strip().strip("'\"")
        if field == "NAME":
            names[sn] = value
        else:
            names.setdefault(sn, sn)
    return sorted(set(names.values()))


def _resolve_name(query: str):
    """Resolve an employee name, accepting a unique prefix ('inf' →
    'influencer-specialist'). Returns (name, []) on success, (None,
    candidates) when ambiguous, (None, []) when nothing matches."""
    q = (query or "").strip().lstrip("@")
    names = _registry_names()
    if q in names:
        return q, []
    ql = q.lower()
    matches = [n for n in names if n.lower().startswith(ql)] if ql else []
    if len(matches) == 1:
        return matches[0], []
    return None, sorted(matches)


def list_orchestrators() -> Dict[str, Any]:
    if manager_team() is None:
        return _err(_NO_TEAM)
    groups = _load_groups()
    fleet = []
    for name in _registry_names():
        st = status(name)
        fleet.append({
            "name": name,
            "team": groups.get(name) or None,
            "state": st.get("state", "unknown"),
            "detail": st.get("detail", ""),
            "target": st.get("target", f"{name}:{name}"),
        })
    return {"ok": True, "team": manager_team(), "orchestrators": fleet}


# ── dispatch / result ──────────────────────────────────────────────────────

def _meta_path(run_id: str) -> Path:
    return runs_dir() / f"{run_id}.json"


def _log_path(run_id: str) -> Path:
    return runs_dir() / f"{run_id}.log"


def _save_meta(meta: Dict[str, Any]) -> None:
    p = _meta_path(meta["run_id"])
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    tmp.replace(p)


def _load_meta(run_id: str) -> Optional[Dict[str, Any]]:
    p = _meta_path(run_id)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return None


_EXIT_CODES = {
    0: "completed",
    2: "timeout (dispatch hard cap reached)",
    3: "failed (worker ended turn without a result, or died)",
    10: "refused (orchestrator busy — retry later)",
    11: "refused (unsent user input on its prompt line)",
    12: "refused (agent auth error)",
    13: "refused (same-cwd peer busy)",
    14: "refused (orchestrator awaiting user input — attach to answer it)",
}


def _finalize(meta: Dict[str, Any], exit_code: int) -> Dict[str, Any]:
    log_text = ""
    try:
        log_text = _log_path(meta["run_id"]).read_text()
    except OSError:
        pass
    meta["exit_code"] = exit_code
    meta["status"] = "completed" if exit_code == 0 else "failed"
    meta["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _save_meta(meta)
    result = log_text.strip()
    truncated = len(result) > RESULT_TRUNCATE_CHARS
    return {
        "ok": exit_code == 0,
        "run_id": meta["run_id"],
        "orchestrator": meta["orchestrator"],
        "status": meta["status"],
        "outcome": _EXIT_CODES.get(exit_code, f"exit code {exit_code}"),
        "result": result[-RESULT_TRUNCATE_CHARS:] if truncated else result,
        **({"result_truncated": True, "full_log": str(_log_path(meta["run_id"]))}
           if truncated else {}),
    }


def _interrupted() -> bool:
    """Best-effort check of hermes' user-interrupt flag (absent outside hermes)."""
    try:
        from tools.interrupt import is_interrupted
        return bool(is_interrupted())
    except Exception:
        return False


def _slack_notify(text: str) -> None:
    """POST a message to the Slack incoming webhook in
    $HERMES_ORCH_SLACK_WEBHOOK. Silently disabled when unset; best-effort
    always — Slack being down must never affect fleet operation.

    Messages are prefixed with the fleet name so several machines can share
    one notification channel and still be told apart."""
    url = os.environ.get("HERMES_ORCH_SLACK_WEBHOOK", "").strip()
    if not url:
        return
    team = manager_team()
    if team:
        text = f"[{team}] {text}"
    try:
        import urllib.request
        req = urllib.request.Request(
            url,
            data=json.dumps({"text": text}).encode(),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=5).read()
    except Exception:
        pass


def _notify(message: str, slack_text: Optional[str] = None) -> None:
    """Push a message into the hermes conversation (no-op outside hermes)
    and, when slack_text is given, mirror a short form to Slack."""
    if slack_text:
        _slack_notify(slack_text)
    if _CTX is None:
        return
    try:
        _CTX.inject_message(message)
    except Exception:
        pass  # a broken notification must never take the watcher down


def _watch_and_notify(run_id: str, proc: subprocess.Popen) -> None:
    """Wait for a backgrounded dispatch to finish, finalize its run record,
    and inject a completion report into the conversation. Runs as a daemon
    thread — one per run that outlived its inline wait window."""
    try:
        code = proc.wait(timeout=int(FLEET_TASK_MAX_SECONDS) + 900)
    except Exception:
        code = None  # stuck beyond every cap — report, don't finalize as done
    _PROCS.pop(run_id, None)

    meta = _load_meta(run_id)
    if meta is None or meta.get("status") == "cancelled":
        return  # interrupted runs were cancelled deliberately — stay quiet

    if code is None:
        _notify(
            f"[fleet-notification] Dispatch run {run_id} for employee "
            f"'{meta.get('orchestrator')}' is STUCK past every timeout cap. "
            f"Tell the user; suggest checking the employee's tmux session.",
            slack_text=(f":warning: *{meta.get('orchestrator')}* run {run_id} "
                        f"is stuck past every timeout cap — check its tmux session."),
        )
        return

    summary = _finalize(meta, code)
    result_preview = (summary.get("result") or "").strip()
    if len(result_preview) > 600:
        result_preview = result_preview[:600] + " …(truncated)"
    emoji = ":white_check_mark:" if summary.get("ok") else ":x:"
    _notify(
        f"[fleet-notification] Dispatch run {run_id} for employee "
        f"'{summary.get('orchestrator')}' just finished — "
        f"{summary.get('outcome')}.\n"
        f"Result:\n{result_preview}\n\n"
        f"Relay this outcome to the user now, briefly and in their language. "
        f"Full text: orchestrator(action='result', run_id='{run_id}').",
        slack_text=(f"{emoji} *{summary.get('orchestrator')}* — "
                    f"{summary.get('outcome')} (run {run_id})\n"
                    f"{result_preview[:300]}"),
    )


def dispatch(name: str, task: str, wait_seconds: int = 120) -> Dict[str, Any]:
    if manager_team() is None:
        return _err(_NO_TEAM)
    if not name or not NAME_RE.match(name.lstrip("@")):
        return _err(f"invalid orchestrator name '{name}'")
    resolved, cands = _resolve_name(name)
    if resolved is None:
        if cands:
            return _err(f"ambiguous employee '{name}' — matches: "
                        f"{', '.join(cands)}. Be more specific.")
        roster = ", ".join(_registry_names()) or "(no employees hired)"
        return _err(f"unknown employee '{name}'. Roster: {roster}")
    name = resolved
    if not task or not task.strip():
        return _err("task must be a non-empty string")
    wait_seconds = max(0, min(int(wait_seconds or 0), DISPATCH_WAIT_CAP_SECONDS))

    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
    log_file = open(_log_path(run_id), "w")
    script = darkarchon_home() / "dispatch-safe.sh"
    env = _env()
    env.setdefault("TASK_MAX_SECONDS", FLEET_TASK_MAX_SECONDS)
    proc = subprocess.Popen(
        [str(script), name, "-"],
        stdin=subprocess.PIPE, stdout=log_file, stderr=subprocess.STDOUT,
        text=True, env=env, cwd=str(darkarchon_home()),
        start_new_session=True,  # keep the poller alive across hermes restarts
    )
    log_file.close()
    proc.stdin.write(task)
    proc.stdin.close()
    _PROCS[run_id] = proc

    meta = {
        "run_id": run_id,
        "orchestrator": name,
        "pid": proc.pid,
        "task_preview": task[:300],
        "status": "running",
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "log": str(_log_path(run_id)),
    }
    _save_meta(meta)

    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        code = proc.poll()
        if code is not None:
            return _finalize(meta, code)
        if _interrupted():
            break
        time.sleep(2)

    # Outlived the inline window: hand off to a watcher thread that will
    # finalize the run and push a completion report into the conversation.
    threading.Thread(
        target=_watch_and_notify, args=(run_id, proc),
        daemon=True, name=f"fleet-watch-{run_id}",
    ).start()
    return {
        "ok": True,
        "run_id": run_id,
        "orchestrator": name,
        "status": "running",
        "note": (
            f"Still running after {wait_seconds}s wait. A completion report "
            f"will be injected into this conversation automatically when it "
            f"finishes — tell the user it's running and END YOUR TURN; do "
            f"not poll. (Manual check: action=result run_id={run_id}.)"
        ),
    }


def result(run_id: str) -> Dict[str, Any]:
    if manager_team() is None:
        return _err(_NO_TEAM)
    meta = _load_meta(run_id)
    if meta is None:
        return _err(f"unknown run_id '{run_id}'")
    if meta.get("status") != "running":
        return _finalize(meta, meta.get("exit_code", 0 if meta["status"] == "completed" else 1))

    proc = _PROCS.get(run_id)
    if proc is not None:
        code = proc.poll()
        if code is not None:
            _PROCS.pop(run_id, None)
            return _finalize(meta, code)
        alive = True
    else:
        # Hermes restarted since dispatch — fall back to pid liveness.
        try:
            os.kill(int(meta["pid"]), 0)
            alive = True
        except (OSError, ValueError):
            alive = False

    log_text = ""
    try:
        log_text = _log_path(run_id).read_text()
    except OSError:
        pass

    if alive:
        return {
            "ok": True,
            "run_id": run_id,
            "orchestrator": meta["orchestrator"],
            "status": "running",
            "log_tail": log_text[-LOG_TAIL_CHARS:],
        }
    # Dispatcher gone without a recorded exit code (restart race). The log
    # holds whatever dispatch-safe printed — surface it and mark finished.
    exit_code = 0 if log_text.strip() and "REFUSED" not in log_text and \
        "TIMEOUT" not in log_text and "NO_RESULT" not in log_text else 1
    return _finalize(meta, exit_code)


def runs(limit: int = 10) -> Dict[str, Any]:
    if manager_team() is None:
        return _err(_NO_TEAM)
    metas = []
    for p in sorted(runs_dir().glob("*.json"), reverse=True)[: max(1, limit)]:
        try:
            m = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        metas.append({k: m.get(k) for k in
                      ("run_id", "orchestrator", "status", "started_at",
                       "finished_at", "task_preview")})
    return {"ok": True, "runs": metas}


# ── invite / uninvite (adopt an EXISTING session as an employee) ───────────

TARGET_RE = re.compile(r"^[a-zA-Z0-9_-]+:[a-zA-Z0-9_.-]+$")


def invite(name: str, target: str, team: str = "") -> Dict[str, Any]:
    """Register an already-running Claude session (tmux session:window) as an
    employee, without spawning anything. Marked EXTERNAL — hermess can
    dispatch to it but never kills it; remove with uninvite."""
    if manager_team() is None:
        return _err(_NO_TEAM)
    if not name or not NAME_RE.match(name):
        return _err(f"invalid employee name '{name}'")
    team = (team or "").strip()
    if team and not NAME_RE.match(team):
        return _err(f"invalid team label '{team}' (use [a-zA-Z0-9_-])")
    target = (target or "").strip()
    if not TARGET_RE.match(target):
        return _err(f"invalid target '{target}' (expected session:window)")
    script = darkarchon_home() / "invite-worker.sh"
    proc = _run([str(script), name, target, "orchestrator"], timeout=30)
    if proc.returncode != 0:
        return _err("invite failed", detail=(proc.stderr or proc.stdout).strip())
    _set_group(name, team)
    return {
        "ok": True,
        "orchestrator": name,
        "team": team or None,
        "tmux_target": target,
        "external": True,
        "note": (
            "Adopted the existing session as an employee. Caveats: its state "
            "is detected by screen-scraping (it has no hook wiring), it did "
            "not receive the orchestrator contract prompt, and its own "
            "sub-team namespace is whatever its environment already uses. "
            "It cannot be killed by the manager — use uninvite to let it go."
        ),
    }


def uninvite(name: str) -> Dict[str, Any]:
    """Drop an invited (external) employee from the registry. The session
    itself is untouched — it belongs to the user."""
    if manager_team() is None:
        return _err(_NO_TEAM)
    if not name or not NAME_RE.match(name):
        return _err(f"invalid employee name '{name}'")
    script = darkarchon_home() / "uninvite-worker.sh"
    proc = _run([str(script), name], timeout=30)
    if proc.returncode != 0:
        return _err("uninvite failed", detail=(proc.stderr or proc.stdout).strip())
    _set_group(name, "")
    return {"ok": True, "orchestrator": name, "message": proc.stdout.strip()}


# ── fleet-level questions (orchestrator → manager escalation) ──────────────

# Questions are files with no push channel — employees drop them silently.
# This watcher polls the fleet queue and raises each NEW pending question
# once through _notify, so "I need a decision" reaches the user without
# anyone running /orch questions. Several hermes processes may run this
# watcher concurrently (the CLI session plus the messaging gateway share
# one HERMES_HOME), so the once-guarantee is a filesystem marker claimed
# with O_EXCL — exactly one process wins the right to notify a question.
_QWATCH_STARTED = False
_QWATCH_SEEN: set = set()  # cheap in-process short-circuit over the markers
QUESTION_POLL_SECONDS = 15


def _claim_once(kind: str, key: str) -> bool:
    """Atomically claim the right to send one notification. First claimer
    across ALL processes wins; markers persist so restarts don't re-notify."""
    d = state_dir() / f"notified-{kind}"
    key = "".join(c if c.isalnum() or c in "-_." else "_" for c in key)[:120]
    try:
        d.mkdir(parents=True, exist_ok=True)
        with open(d / key, "x") as fh:
            fh.write(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        return True
    except FileExistsError:
        return False
    except OSError:
        return False  # cannot prove we're first — better silent than twice


def _claim_question_notification(qid: str) -> bool:
    return _claim_once("questions", qid)


# ── direct-work notifications ───────────────────────────────────────────────
# The user often attaches to an employee's pane and works there directly —
# no dispatch, so the run watcher never sees it. Spawned employees' state
# hooks record every turn regardless of who typed, so this checker turns
# state-file transitions into Slack pings:
#   busy → idle  (turn ≥ HERMES_ORCH_DIRECT_NOTIFY_MIN_SECONDS, default 60,
#                 and no dispatch run attached)  → "finished a direct turn"
#   → awaiting_user (permission prompt etc., any time) → "needs your input"
# Disable with HERMES_ORCH_DIRECT_NOTIFY=0. Invited (hook-less) employees
# have no state file and are not covered.
_PREV_STATE: Dict[str, Dict[str, Any]] = {}


def _direct_notify_enabled() -> bool:
    return os.environ.get("HERMES_ORCH_DIRECT_NOTIFY", "1").strip().lower() \
        not in ("0", "false", "no", "off")


def _dispatch_recently_active(name: str, within_seconds: int = 180) -> bool:
    """True when a dispatch run for `name` is running or JUST finished.

    The run watcher owns notifications for dispatched work; without the
    just-finished grace window a run that finalizes a moment before the
    worker's Stop hook lands would make its turn look direct-typed and
    get double-reported."""
    import calendar
    now = time.time()
    for p in sorted(runs_dir().glob("*.json"), reverse=True)[:20]:
        try:
            m = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if m.get("orchestrator") != name:
            continue
        if m.get("status") == "running":
            return True
        fin = m.get("finished_at") or ""
        try:
            fin_epoch = calendar.timegm(
                time.strptime(fin, "%Y-%m-%dT%H:%M:%SZ"))
            if now - fin_epoch <= within_seconds:
                return True
        except (ValueError, OverflowError):
            continue
    return False


def _check_direct_transitions() -> None:
    if not _direct_notify_enabled():
        return
    min_secs = int(os.environ.get(
        "HERMES_ORCH_DIRECT_NOTIFY_MIN_SECONDS", "60") or 60)
    for name in _registry_names():
        f = state_dir() / "states" / f"{_safe_name(name)}.json"
        try:
            cur = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        st = cur.get("state") or ""
        ts = int(cur.get("ts_epoch") or 0)
        prev = _PREV_STATE.get(name)
        _PREV_STATE[name] = {"state": st, "ts": ts}
        if prev is None or prev["state"] == st:
            continue  # first sighting or no transition — never notify on startup

        if st == "idle" and prev["state"] in ("busy", "compacting"):
            dur = max(0, ts - int(prev["ts"] or ts))
            if dur >= min_secs and not _dispatch_recently_active(name) \
                    and _claim_once("direct", f"{_safe_name(name)}-{ts}"):
                _slack_notify(
                    f":zzz: *{name}* finished a direct-work turn "
                    f"({dur // 60}m {dur % 60}s) — typed in its pane, "
                    f"no dispatch attached."
                )
        elif st == "awaiting_user":
            # A permission prompt / question stalls the pane whether the work
            # was dispatched or typed — always worth a ping.
            detail = (cur.get("detail") or "").strip()
            if _claim_once("direct", f"{_safe_name(name)}-await-{ts}"):
                _slack_notify(
                    f":keyboard: *{name}* is waiting for your input"
                    + (f": {detail[:150]}" if detail else "")
                    + f" — attach: tmux attach -t {name}"
                )


def _questions_watcher() -> None:
    while True:
        try:
            if manager_team() is not None:
                qdir = state_dir() / "questions"
                files = sorted(qdir.glob("*.json")) if qdir.is_dir() else []
                for p in files:
                    try:
                        q = json.loads(p.read_text())
                    except (OSError, json.JSONDecodeError):
                        continue
                    qid = q.get("question_id") or p.stem
                    if q.get("status") != "pending" or qid in _QWATCH_SEEN:
                        continue
                    _QWATCH_SEEN.add(qid)
                    if not _claim_question_notification(qid):
                        continue  # another hermes process already notified it
                    body = (q.get("body") or "").strip()
                    frm = q.get("from_worker", "?")
                    _notify(
                        f"[fleet-notification] Employee '{frm}' escalated a "
                        f"question that needs a HUMAN decision "
                        f"(id {qid}):\n{body[:500]}\n\n"
                        f"Relay it to the user now, in their language. Never "
                        f"answer it yourself — when the user decides, send it "
                        f"back with orchestrator(action='answer', "
                        f"question_id='{qid}', answer=<their decision>).",
                        slack_text=(f":question: *{frm}* needs your decision "
                                    f"(id {qid}):\n{body[:300]}"),
                    )
        except Exception:
            pass  # the watcher must survive anything
        try:
            _check_direct_transitions()
        except Exception:
            pass
        time.sleep(QUESTION_POLL_SECONDS)


def _ensure_question_watcher() -> None:
    global _QWATCH_STARTED
    if _QWATCH_STARTED:
        return
    _QWATCH_STARTED = True
    threading.Thread(target=_questions_watcher, daemon=True,
                     name="fleet-question-watch").start()


def questions() -> Dict[str, Any]:
    """Pending questions orchestrators filed via ask (they have no other
    push channel up to the manager — surface these to the user)."""
    if manager_team() is None:
        return _err(_NO_TEAM)
    qdir = state_dir() / "questions"
    pending = []
    for p in sorted(qdir.glob("*.json")):
        try:
            q = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if q.get("status") == "pending":
            pending.append({k: q.get(k) for k in
                            ("question_id", "from_worker", "body", "created_at")})
    return {"ok": True, "team": manager_team(), "questions": pending}


def answer(question_id: str, text: str) -> Dict[str, Any]:
    """Answer a pending question — delivered to the asking orchestrator's
    mailbox (it drains on its next MAILBOX_NOTIFY / dispatch turn)."""
    if manager_team() is None:
        return _err(_NO_TEAM)
    question_id = (question_id or "").strip()
    if not question_id or "/" in question_id or ".." in question_id:
        return _err(f"invalid question_id '{question_id}'")
    if not text or not text.strip():
        return _err("answer text must be non-empty")
    script = darkarchon_home() / "questions.sh"
    proc = _run([str(script), "answer", question_id, text], timeout=30)
    if proc.returncode != 0:
        return _err("answer failed", detail=(proc.stderr or proc.stdout).strip())
    return {"ok": True, "question_id": question_id,
            "message": proc.stdout.strip()}


def interrupt(run_id: str) -> Dict[str, Any]:
    """Stop the dispatch poller for a run (the orchestrator itself keeps going)."""
    if manager_team() is None:
        return _err(_NO_TEAM)
    meta = _load_meta(run_id)
    if meta is None:
        return _err(f"unknown run_id '{run_id}'")
    # Mark cancelled BEFORE signalling: the completion watcher wakes on the
    # SIGTERM and must see the cancellation, not report a spurious failure.
    meta["status"] = "cancelled"
    _save_meta(meta)
    try:
        os.killpg(int(meta["pid"]), signal.SIGTERM)
    except (OSError, ValueError) as exc:
        return _err(f"could not signal dispatcher: {exc}")
    return {"ok": True, "run_id": run_id, "status": "cancelled",
            "note": "Dispatcher stopped. The orchestrator session was not killed."}
