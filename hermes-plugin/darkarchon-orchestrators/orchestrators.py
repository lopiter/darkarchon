"""Core logic for the darkarchon-orchestrators Hermes plugin.

Hermes acts as a *fleet manager*: it spawns Claude Code "orchestrator"
sessions in tmux (via darkarchon's spawn-worker.sh, role=orchestrator) and
dispatches tasks to them with darkarchon's file-based protocol
(prompt file → tmux trigger → result file). Each orchestrator in turn runs
its own darkarchon worker team, namespaced by DARKARCHON_TEAM=<its name>.

Everything here shells out to the battle-tested darkarchon scripts — this
module adds only fleet-level bookkeeping:

- teams: an employee is hired INTO a named darkarchon team, asked from the
  user at hire time (spawn/invite refuse without one). The team IS the
  darkarchon namespace — its registry and state live in ~/.darkarchon/<team>/,
  exactly like a team the user creates by hand with DARKARCHON_TEAM=<team>.
  Several teams coexist; the known ones are remembered in
  ~/.hermes/darkarchon-orchestrators.json and every read-side action (list,
  status, dispatch, runs, questions) spans all of them, so no roster ever
  goes invisible. HERMES_ORCH_TEAM still pins one team for a process.
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


def _load_team_state() -> Dict[str, Any]:
    try:
        d = json.loads(_team_state_file().read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_team_state(state: Dict[str, Any]) -> None:
    f = _team_state_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False))
    tmp.replace(f)


def current_team() -> Optional[str]:
    """The team new work defaults to: HERMES_ORCH_TEAM env (pins a process to
    one team) > the team of the most recent hire. None before the first hire —
    which is fine, because hires always name their team explicitly."""
    env = os.environ.get("HERMES_ORCH_TEAM")
    if env:
        return env
    team = _load_team_state().get("team")
    return str(team) if team else None


# Legacy name from when there was exactly one fleet namespace. Kept so old
# callers (and the /orch command) keep working.
manager_team = current_team


def known_teams() -> List[str]:
    """Every team hermes has hired into (plus any env-pinned one). These are
    plain darkarchon teams — identical to ones the user makes by hand."""
    st = _load_team_state()
    teams = {str(t) for t in (st.get("teams") or []) if t}
    for t in (st.get("team"), os.environ.get("HERMES_ORCH_TEAM")):
        if t:
            teams.add(str(t))
    return sorted(teams)


def remember_team(team: str, make_current: bool = True) -> None:
    st = _load_team_state()
    # Seed from known_teams(), not st["teams"] — a state file written before
    # teams existed carries its only team in "team", and rebuilding the list
    # from the (absent) "teams" key would drop that team and hide its roster.
    st["teams"] = sorted(set(known_teams()) | {team})
    if make_current:
        st["team"] = team
    _save_team_state(st)


def _ask_team(action: str) -> str:
    """Error text for a hire with no team named. Hiring must never invent a
    team: the name decides which ~/.darkarchon/<team>/ the employee lands in,
    and the user is the one who knows how they want their staff split."""
    existing = known_teams()
    roster = (f"Existing teams: {', '.join(existing)}. "
              if existing else "There are no teams yet. ")
    return (
        f"No team named for this hire. STOP — do NOT retry {action} with a "
        f"team you made up. End your turn by ASKING THE USER which team this "
        f"employee joins (an existing one, or a new name they choose). "
        f"{roster}The team is a real darkarchon namespace: registry and state "
        f"live in ~/.darkarchon/<team>/. Call {action} again in a later turn "
        f"with team=<what the user typed>."
    )


def set_team(team: str, force: bool = False) -> Dict[str, Any]:
    """Create a team (or switch the default to an existing one). Rarely
    needed: hiring with team=<name> creates it on the spot. Nothing is
    hidden by switching — every other team stays visible to list/dispatch."""
    team = (team or "").strip()
    if not team or not NAME_RE.match(team):
        return _err(f"invalid team name '{team}' (use [a-zA-Z0-9_-])")
    existed = team in known_teams()
    remember_team(team)
    return {"ok": True, "team": team, "teams": known_teams(),
            "note": (f"Default team is now '{team}' "
                     f"({'existing' if existed else 'new'}); registry and "
                     f"state live in ~/.darkarchon/{team}/. Employees in "
                     f"other teams remain visible and dispatchable.")}


# Deprecated alias — the fleet used to be a single global namespace.
set_fleet = set_team


def teams() -> Dict[str, Any]:
    """All teams with their rosters."""
    out = []
    for t in known_teams():
        out.append({"team": t, "employees": _registry_names(t),
                    "current": t == current_team()})
    return {"ok": True, "team": current_team(), "teams": out}


def state_dir(team: Optional[str] = None) -> Path:
    prefix = os.environ.get("TOOL_PREFIX", "darkarchon")
    return Path.home() / f".{prefix}" / str(team or current_team())


def runs_dir(team: Optional[str] = None) -> Path:
    d = state_dir(team) / "hermes-runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _env(extra: Optional[Dict[str, str]] = None,
         team: Optional[str] = None) -> Dict[str, str]:
    """Env for team-level darkarchon calls: the target team's namespace."""
    env = dict(os.environ)
    env["DARKARCHON_TEAM"] = str(team or current_team())
    # Hermes itself is not a darkarchon worker — make sure no inherited worker
    # identity can shadow the team namespace in the python resolvers.
    env.pop("EE_STATE_DIR", None)
    env.pop("STATE_DIR", None)
    if extra:
        env.update(extra)
    return env


def _run(cmd: List[str], timeout: int = 60,
         extra_env: Optional[Dict[str, str]] = None,
         team: Optional[str] = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout,
        env=_env(extra_env, team), cwd=str(darkarchon_home()),
    )


def _err(message: str, **extra: Any) -> Dict[str, Any]:
    out = {"ok": False, "error": message}
    out.update(extra)
    return out


# ── spawn / kill ───────────────────────────────────────────────────────────

def _safe_name(name: str) -> str:
    """Same scheme as safe_name() in lib/_lib.sh / worker_state.py."""
    return "".join(c if c.isalnum() or c == "_" else "_" for c in name)


def _recorded_session_id(name: str, team: Optional[str] = None) -> str:
    """Last Claude session id the state hook recorded for this employee."""
    f = state_dir(team) / "states" / f"{_safe_name(name)}.json"
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
    if not name or not NAME_RE.match(name):
        return _err(f"invalid orchestrator name '{name}' (use [a-zA-Z0-9_-])")
    team = (team or "").strip()
    if not team:
        return _err(_ask_team("spawn"))
    if not NAME_RE.match(team):
        return _err(f"invalid team name '{team}' (use [a-zA-Z0-9_-])")
    if name == team:
        return _err(f"employee name '{name}' collides with its team namespace")
    # Names are resolved across every team, so the same one cannot live twice.
    other = _team_of(name)
    if other and other != team:
        return _err(
            f"'{name}' is already registered in team '{other}'. Employee "
            f"names are unique across teams (dispatch/status resolve by name "
            f"alone). Use team='{other}' for that employee, or pick another "
            f"name for a new hire."
        )
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
        if name in _registry_names(team):
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
        resume_id = _recorded_session_id(name, team)
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
        if name in _registry_names(team):
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
    if probe.returncode == 0 and name not in _registry_names(team):
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
        ctx_dir = state_dir(team) / "context" / name
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
    # Lineage for the dashboard graph: hermes is usually not a spawned worker
    # itself (no EE_WORKER_NAME), so spawn-worker.sh's automatic default would
    # record nothing — pass the manager identity explicitly.
    spawner = os.environ.get("EE_WORKER_NAME") or team
    if spawner:
        args += ["--spawned-by", spawner]
    args += [name, str(workdir), "orchestrator"]
    proc = _run(args, timeout=30, extra_env=extra_env, team=team)
    if proc.returncode != 0:
        return _err("spawn failed", detail=(proc.stderr or proc.stdout).strip())
    remember_team(team)
    return {
        "ok": True,
        "orchestrator": name,
        "team": team,
        "tmux_target": f"{name}:{name}",
        "tmux_session": name,
        "cwd": str(workdir),
        "resumed": bool(resume_id),
        "continued_session": session_id or None,
        "note": (
            f"Hired into team '{team}' (~/.darkarchon/{team}/). "
            + (f"Hired onto existing Claude session {session_id} — it continues "
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
    if not name or not NAME_RE.match(name):
        return _err(f"invalid orchestrator name '{name}'")
    team = _team_of(name)
    if team is None:
        return _err(f"unknown employee '{name}' in any team "
                    f"({', '.join(known_teams()) or 'none'})")
    script = darkarchon_home() / "lib" / "kill-worker.sh"
    proc = _run([str(script), name], timeout=30, team=team)
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
    return {"ok": True, "orchestrator": name, "team": team,
            "message": proc.stdout.strip(),
            "note": f"tmux session '{name}' (employee + its workers) removed."}


# ── state / list ───────────────────────────────────────────────────────────

def status(name: str) -> Dict[str, Any]:
    if not name or not NAME_RE.match(name.lstrip("@")):
        return _err(f"invalid orchestrator name '{name}'")
    resolved, team, cands = _resolve_name(name)
    if resolved is None and cands:
        return _err(f"ambiguous employee '{name}' — matches: {', '.join(cands)}")
    if resolved:
        name = resolved
    script = darkarchon_home() / "lib" / "worker_state.py"
    proc = _run(["python3", str(script), name, "--json"], timeout=30, team=team)
    if proc.returncode != 0:
        return _err("state resolution failed",
                    detail=(proc.stderr or proc.stdout).strip())
    try:
        return {"ok": True, "orchestrator": name, "team": team,
                **json.loads(proc.stdout)}
    except json.JSONDecodeError:
        return {"ok": True, "orchestrator": name, "team": team,
                "raw": proc.stdout.strip()}


def _registry_names(team: Optional[str] = None) -> List[str]:
    """Orchestrator names from a team's runtime registry."""
    reg = state_dir(team) / "workers-runtime.env"
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


def _all_employees() -> List[tuple]:
    """(name, team) for every employee across all known teams. A name can
    only belong to one team — darkarchon registries key on it — so the first
    team claiming a name wins if two ever collide."""
    seen: Dict[str, str] = {}
    for team in known_teams():
        for name in _registry_names(team):
            seen.setdefault(name, team)
    return sorted(seen.items())


def _team_of(name: str) -> Optional[str]:
    """The team an employee is registered in, searched across all of them."""
    for n, team in _all_employees():
        if n == name:
            return team
    return None


def _resolve_name(query: str):
    """Resolve an employee name across all teams, accepting a unique prefix
    ('inf' → 'influencer-specialist'). Returns (name, team, []) on success,
    (None, None, candidates) when ambiguous, (None, None, []) when nothing
    matches."""
    q = (query or "").strip().lstrip("@")
    pairs = _all_employees()
    for n, team in pairs:
        if n == q:
            return q, team, []
    ql = q.lower()
    matches = [(n, t) for n, t in pairs if n.lower().startswith(ql)] if ql else []
    if len(matches) == 1:
        return matches[0][0], matches[0][1], []
    return None, None, sorted(n for n, _ in matches)


def list_orchestrators() -> Dict[str, Any]:
    fleet = []
    for name, team in _all_employees():
        st = status(name)
        fleet.append({
            "name": name,
            "team": team,
            "state": st.get("state", "unknown"),
            "detail": st.get("detail", ""),
            "target": st.get("target", f"{name}:{name}"),
        })
    return {"ok": True, "team": current_team(), "teams": known_teams(),
            "orchestrators": fleet}


# ── dispatch / result ──────────────────────────────────────────────────────

def _meta_path(run_id: str, team: Optional[str] = None) -> Path:
    return runs_dir(team) / f"{run_id}.json"


def _log_path(run_id: str, team: Optional[str] = None) -> Path:
    return runs_dir(team) / f"{run_id}.log"


def _save_meta(meta: Dict[str, Any]) -> None:
    p = _meta_path(meta["run_id"], meta.get("team"))
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    tmp.replace(p)


def _load_meta(run_id: str) -> Optional[Dict[str, Any]]:
    """Runs live under their team's state dir; a run_id identifies exactly one
    run, so look through every team the manager knows."""
    for team in known_teams() or [current_team()]:
        p = _meta_path(run_id, team)
        if not p.is_file():
            continue
        try:
            meta = json.loads(p.read_text())
        except json.JSONDecodeError:
            return None
        meta.setdefault("team", team)  # runs recorded before teams existed
        return meta
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
    log = _log_path(meta["run_id"], meta.get("team"))
    log_text = ""
    try:
        log_text = log.read_text()
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
        "team": meta.get("team"),
        "status": meta["status"],
        "outcome": _EXIT_CODES.get(exit_code, f"exit code {exit_code}"),
        "result": result[-RESULT_TRUNCATE_CHARS:] if truncated else result,
        **({"result_truncated": True, "full_log": str(log)}
           if truncated else {}),
    }


def _interrupted() -> bool:
    """Best-effort check of hermes' user-interrupt flag (absent outside hermes)."""
    try:
        from tools.interrupt import is_interrupted
        return bool(is_interrupted())
    except Exception:
        return False


def _slack_notify(text: str, team: Optional[str] = None) -> None:
    """POST a message to the Slack incoming webhook in
    $HERMES_ORCH_SLACK_WEBHOOK. Silently disabled when unset; best-effort
    always — Slack being down must never affect fleet operation.

    Messages are prefixed with the team name so several teams (and machines)
    can share one notification channel and still be told apart."""
    url = os.environ.get("HERMES_ORCH_SLACK_WEBHOOK", "").strip()
    if not url:
        return
    team = team or current_team()
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


def _origin_session_id() -> Optional[str]:
    """Dashboard session that invoked the current tool call, or None.

    Must be called from inside the tool call itself: hermes binds the id to
    the turn thread as a ContextVar (HERMES_UI_SESSION_ID), and watcher
    threads spawned later never inherit it. CLI runs (and hermes builds
    without the gateway session context) return None — there is a single
    conversation, so no routing is needed."""
    try:
        from gateway.session_context import get_session_env

        return get_session_env("HERMES_UI_SESSION_ID", "") or None
    except Exception:
        return None


def _origin_session_key() -> Optional[str]:
    """Durable key of the conversation that invoked the current tool call.

    Captured alongside _origin_session_id() and for the same reason (the
    ContextVar is bound to the turn thread only), but it outlives the live
    session: when a remote desktop's connection drops, hermes reaps the live
    session while the conversation itself survives and returns under a new
    UI id. A dispatch that outlasts the connection can only be reported back
    by this key — see _notify()."""
    try:
        from gateway.session_context import get_session_env

        return get_session_env("HERMES_SESSION_KEY", "") or None
    except Exception:
        return None


def _notify(message: str, slack_text: Optional[str] = None,
            session_id: Optional[str] = None,
            session_key: Optional[str] = None,
            team: Optional[str] = None) -> None:
    """Push a message into the hermes conversation (no-op outside hermes)
    and, when slack_text is given, mirror a short form to Slack.

    session_id targets the dashboard session that started the run, so a
    report never lands in a concurrently open sibling session. session_key
    is the durable handle for that same conversation: dispatches routinely
    outlive a remote client's connection, and once the live session has been
    reaped the key is the only thing that still identifies where the report
    belongs — hermes delivers on it, or holds the report until the
    conversation comes back. Older hermes builds accept neither; the report
    then goes untargeted, and the Slack mirror above has already gone out
    either way."""
    if slack_text:
        _slack_notify(slack_text, team)
    if _CTX is None:
        return
    try:
        if session_id is None and session_key is None:
            _CTX.inject_message(message)
            return
        try:
            _CTX.inject_message(
                message, session_id=session_id, session_key=session_key
            )
            return
        except TypeError:
            pass
        try:
            _CTX.inject_message(message, session_id=session_id)
            return
        except TypeError:
            # hermes without per-session routing — deliver untargeted
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
    origin = meta.get("origin_session") or None
    origin_key = meta.get("origin_session_key") or None

    if code is None:
        _notify(
            f"[fleet-notification] Dispatch run {run_id} for employee "
            f"'{meta.get('orchestrator')}' is STUCK past every timeout cap. "
            f"Tell the user; suggest checking the employee's tmux session.",
            slack_text=(f":warning: *{meta.get('orchestrator')}* run {run_id} "
                        f"is stuck past every timeout cap — check its tmux session."),
            session_id=origin,
            session_key=origin_key,
            team=meta.get("team"),
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
        session_id=origin,
        session_key=origin_key,
        team=meta.get("team"),
    )


def dispatch(name: str, task: str, wait_seconds: int = 120) -> Dict[str, Any]:
    if not name or not NAME_RE.match(name.lstrip("@")):
        return _err(f"invalid orchestrator name '{name}'")
    resolved, team, cands = _resolve_name(name)
    if resolved is None:
        if cands:
            return _err(f"ambiguous employee '{name}' — matches: "
                        f"{', '.join(cands)}. Be more specific.")
        roster = ", ".join(n for n, _ in _all_employees()) or "(no employees hired)"
        return _err(f"unknown employee '{name}'. Roster: {roster}")
    name = resolved
    if not task or not task.strip():
        return _err("task must be a non-empty string")
    wait_seconds = max(0, min(int(wait_seconds or 0), DISPATCH_WAIT_CAP_SECONDS))

    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
    log_file = open(_log_path(run_id, team), "w")
    script = darkarchon_home() / "dispatch-safe.sh"
    env = _env(team=team)
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
        "team": team,
        "pid": proc.pid,
        "task_preview": task[:300],
        "status": "running",
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "log": str(_log_path(run_id, team)),
        # Captured here, on the turn thread — the completion report must go
        # back to the dashboard session that asked for this dispatch.
        "origin_session": _origin_session_id(),
        "origin_session_key": _origin_session_key(),
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
        "team": team,
        "status": "running",
        "note": (
            f"Still running after {wait_seconds}s wait. A completion report "
            f"will be injected into this conversation automatically when it "
            f"finishes — tell the user it's running and END YOUR TURN; do "
            f"not poll. (Manual check: action=result run_id={run_id}.)"
        ),
    }


def result(run_id: str) -> Dict[str, Any]:
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
        log_text = _log_path(run_id, meta.get("team")).read_text()
    except OSError:
        pass

    if alive:
        return {
            "ok": True,
            "run_id": run_id,
            "orchestrator": meta["orchestrator"],
            "team": meta.get("team"),
            "status": "running",
            "log_tail": log_text[-LOG_TAIL_CHARS:],
        }
    # Dispatcher gone without a recorded exit code (restart race). The log
    # holds whatever dispatch-safe printed — surface it and mark finished.
    exit_code = 0 if log_text.strip() and "REFUSED" not in log_text and \
        "TIMEOUT" not in log_text and "NO_RESULT" not in log_text else 1
    return _finalize(meta, exit_code)


def runs(limit: int = 10) -> Dict[str, Any]:
    """Recent runs across every team (run ids are timestamp-prefixed, so
    sorting the merged list by name is chronological)."""
    files = []
    for team in known_teams():
        files += [(p.name, team, p) for p in runs_dir(team).glob("*.json")]
    metas = []
    for _, team, p in sorted(files, reverse=True)[: max(1, limit)]:
        try:
            m = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        m.setdefault("team", team)
        metas.append({k: m.get(k) for k in
                      ("run_id", "orchestrator", "team", "status",
                       "started_at", "finished_at", "task_preview")})
    return {"ok": True, "runs": metas}


# ── invite / uninvite (adopt an EXISTING session as an employee) ───────────

TARGET_RE = re.compile(r"^[a-zA-Z0-9_-]+:[a-zA-Z0-9_.-]+$")


def invite(name: str, target: str, team: str = "") -> Dict[str, Any]:
    """Register an already-running Claude session (tmux session:window) as an
    employee, without spawning anything. Marked EXTERNAL — hermess can
    dispatch to it but never kills it; remove with uninvite."""
    if not name or not NAME_RE.match(name):
        return _err(f"invalid employee name '{name}'")
    team = (team or "").strip()
    if not team:
        return _err(_ask_team("invite"))
    if not NAME_RE.match(team):
        return _err(f"invalid team name '{team}' (use [a-zA-Z0-9_-])")
    other = _team_of(name)
    if other and other != team:
        return _err(
            f"'{name}' is already registered in team '{other}'. Employee "
            f"names are unique across teams — uninvite it first, or use a "
            f"different name."
        )
    target = (target or "").strip()
    if not TARGET_RE.match(target):
        return _err(f"invalid target '{target}' (expected session:window)")
    script = darkarchon_home() / "invite-worker.sh"
    proc = _run([str(script), name, target, "orchestrator"], timeout=30,
                team=team)
    if proc.returncode != 0:
        return _err("invite failed", detail=(proc.stderr or proc.stdout).strip())
    remember_team(team)
    return {
        "ok": True,
        "orchestrator": name,
        "team": team,
        "tmux_target": target,
        "external": True,
        "note": (
            f"Adopted the existing session as an employee of team '{team}' "
            f"(~/.darkarchon/{team}/). Caveats: its state "
            "is detected by screen-scraping (it has no hook wiring), it did "
            "not receive the orchestrator contract prompt, and its own "
            "sub-team namespace is whatever its environment already uses. "
            "It cannot be killed by the manager — use uninvite to let it go."
        ),
    }


def uninvite(name: str) -> Dict[str, Any]:
    """Drop an invited (external) employee from the registry. The session
    itself is untouched — it belongs to the user."""
    if not name or not NAME_RE.match(name):
        return _err(f"invalid employee name '{name}'")
    team = _team_of(name)
    if team is None:
        return _err(f"unknown employee '{name}' in any team "
                    f"({', '.join(known_teams()) or 'none'})")
    script = darkarchon_home() / "uninvite-worker.sh"
    proc = _run([str(script), name], timeout=30, team=team)
    if proc.returncode != 0:
        return _err("uninvite failed", detail=(proc.stderr or proc.stdout).strip())
    return {"ok": True, "orchestrator": name, "team": team,
            "message": proc.stdout.strip()}


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


def _claim_once(kind: str, key: str, team: Optional[str] = None) -> bool:
    """Atomically claim the right to send one notification. First claimer
    across ALL processes wins; markers persist so restarts don't re-notify."""
    d = state_dir(team) / f"notified-{kind}"
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


def _claim_question_notification(qid: str, team: Optional[str] = None) -> bool:
    return _claim_once("questions", qid, team)


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


def _dispatch_recently_active(name: str, within_seconds: int = 180,
                              team: Optional[str] = None) -> bool:
    """True when a dispatch run for `name` is running or JUST finished.

    The run watcher owns notifications for dispatched work; without the
    just-finished grace window a run that finalizes a moment before the
    worker's Stop hook lands would make its turn look direct-typed and
    get double-reported."""
    import calendar
    now = time.time()
    for p in sorted(runs_dir(team).glob("*.json"), reverse=True)[:20]:
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
    for name, team in _all_employees():
        f = state_dir(team) / "states" / f"{_safe_name(name)}.json"
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
            if dur >= min_secs and not _dispatch_recently_active(name, team=team) \
                    and _claim_once("direct", f"{_safe_name(name)}-{ts}", team):
                _slack_notify(
                    f":zzz: *{name}* finished a direct-work turn "
                    f"({dur // 60}m {dur % 60}s) — typed in its pane, "
                    f"no dispatch attached.", team
                )
        elif st in ("awaiting_user", "awaiting_permission"):
            # A permission prompt / question stalls the pane whether the work
            # was dispatched or typed — always worth a ping.
            detail = (cur.get("detail") or "").strip()
            if _claim_once("direct", f"{_safe_name(name)}-await-{ts}", team):
                _slack_notify(
                    f":keyboard: *{name}* is waiting for your input"
                    + (f": {detail[:150]}" if detail else "")
                    + f" — attach: tmux attach -t {name}", team
                )


def _questions_watcher() -> None:
    while True:
        try:
            for team in known_teams():
                qdir = state_dir(team) / "questions"
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
                    if not _claim_question_notification(qid, team):
                        continue  # another hermes process already notified it
                    body = (q.get("body") or "").strip()
                    frm = q.get("from_worker", "?")
                    _notify(
                        f"[fleet-notification] Employee '{frm}' (team "
                        f"'{team}') escalated a question that needs a HUMAN "
                        f"decision (id {qid}):\n{body[:500]}\n\n"
                        f"Relay it to the user now, in their language. Never "
                        f"answer it yourself — when the user decides, send it "
                        f"back with orchestrator(action='answer', "
                        f"question_id='{qid}', answer=<their decision>).",
                        slack_text=(f":question: *{frm}* needs your decision "
                                    f"(id {qid}):\n{body[:300]}"),
                        team=team,
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
    pending = []
    for team in known_teams():
        qdir = state_dir(team) / "questions"
        for p in (sorted(qdir.glob("*.json")) if qdir.is_dir() else []):
            try:
                q = json.loads(p.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            if q.get("status") == "pending":
                item = {k: q.get(k) for k in
                        ("question_id", "from_worker", "body", "created_at")}
                item["team"] = team
                pending.append(item)
    return {"ok": True, "team": current_team(), "questions": pending}


def _team_of_question(question_id: str) -> Optional[str]:
    for team in known_teams():
        if (state_dir(team) / "questions" / f"{question_id}.json").is_file():
            return team
    return None


def answer(question_id: str, text: str) -> Dict[str, Any]:
    """Answer a pending question — delivered to the asking orchestrator's
    mailbox (it drains on its next MAILBOX_NOTIFY / dispatch turn)."""
    question_id = (question_id or "").strip()
    if not question_id or "/" in question_id or ".." in question_id:
        return _err(f"invalid question_id '{question_id}'")
    if not text or not text.strip():
        return _err("answer text must be non-empty")
    team = _team_of_question(question_id)
    if team is None:
        return _err(f"unknown question_id '{question_id}' in any team "
                    f"({', '.join(known_teams()) or 'none'})")
    script = darkarchon_home() / "questions.sh"
    proc = _run([str(script), "answer", question_id, text], timeout=30,
                team=team)
    if proc.returncode != 0:
        return _err("answer failed", detail=(proc.stderr or proc.stdout).strip())
    return {"ok": True, "question_id": question_id, "team": team,
            "message": proc.stdout.strip()}


def interrupt(run_id: str) -> Dict[str, Any]:
    """Stop the dispatch poller for a run (the orchestrator itself keeps going)."""
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
