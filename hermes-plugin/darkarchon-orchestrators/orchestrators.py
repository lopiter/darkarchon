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
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

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
    chose at first use (persisted by set_team). No default — the user names
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
    "No fleet name is set yet. STOP — do NOT call set_team in this turn, and "
    "do NOT make up a name. End your turn by ASKING THE USER what to name the "
    "fleet (it namespaces state under ~/.darkarchon/<name>/). Call "
    "action=set_team only in a later turn, with the name the user typed."
)


def set_team(team: str) -> Dict[str, Any]:
    team = (team or "").strip()
    if not team or not NAME_RE.match(team):
        return _err(f"invalid team name '{team}' (use [a-zA-Z0-9_-])")
    f = _team_state_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"team": team}))
    return {"ok": True, "team": team,
            "note": f"Fleet name set. Registry and state live in "
                    f"~/.darkarchon/{team}/; each employee gets its own tmux "
                    f"session named after it."}


def state_dir() -> Path:
    prefix = os.environ.get("TOOL_PREFIX", "darkarchon")
    return Path.home() / f".{prefix}" / str(manager_team())


def runs_dir() -> Path:
    d = state_dir() / "hermes-runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


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

def spawn(name: str, cwd: str, brief: str = "") -> Dict[str, Any]:
    if manager_team() is None:
        return _err(_NO_TEAM)
    if not name or not NAME_RE.match(name):
        return _err(f"invalid orchestrator name '{name}' (use [a-zA-Z0-9_-])")
    if name == manager_team():
        return _err(f"name '{name}' collides with the manager team namespace")
    workdir = Path(cwd).expanduser()
    if not workdir.is_dir():
        return _err(f"cwd not found: {workdir}")

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
    proc = _run(
        [str(script), "--env", f"DARKARCHON_TEAM={name}", "--session", name,
         name, str(workdir), "orchestrator"],
        timeout=30, extra_env=extra_env,
    )
    if proc.returncode != 0:
        return _err("spawn failed", detail=(proc.stderr or proc.stdout).strip())
    return {
        "ok": True,
        "orchestrator": name,
        "tmux_target": f"{name}:{name}",
        "tmux_session": name,
        "cwd": str(workdir),
        "note": (
            "Claude takes ~15s to start. If a trust prompt appears in the tmux "
            "window the user must attach and hit Enter once. Check readiness "
            "with action=status before the first dispatch."
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
    return {"ok": True, "orchestrator": name,
            "message": proc.stdout.strip(),
            "note": f"tmux session '{name}' (employee + its workers) removed."}


# ── state / list ───────────────────────────────────────────────────────────

def status(name: str) -> Dict[str, Any]:
    if manager_team() is None:
        return _err(_NO_TEAM)
    if not name or not NAME_RE.match(name):
        return _err(f"invalid orchestrator name '{name}'")
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


def list_orchestrators() -> Dict[str, Any]:
    if manager_team() is None:
        return _err(_NO_TEAM)
    fleet = []
    for name in _registry_names():
        st = status(name)
        fleet.append({
            "name": name,
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


def dispatch(name: str, task: str, wait_seconds: int = 120) -> Dict[str, Any]:
    if manager_team() is None:
        return _err(_NO_TEAM)
    if not name or not NAME_RE.match(name):
        return _err(f"invalid orchestrator name '{name}'")
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

    return {
        "ok": True,
        "run_id": run_id,
        "orchestrator": name,
        "status": "running",
        "note": (
            f"Still running after {wait_seconds}s wait. Poll with "
            f"action=result run_id={run_id}. Long tasks are normal — the "
            "orchestrator may be coordinating its own worker team."
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


# ── fleet-level questions (orchestrator → manager escalation) ──────────────

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
    try:
        os.killpg(int(meta["pid"]), signal.SIGTERM)
    except (OSError, ValueError) as exc:
        return _err(f"could not signal dispatcher: {exc}")
    meta["status"] = "cancelled"
    _save_meta(meta)
    return {"ok": True, "run_id": run_id, "status": "cancelled",
            "note": "Dispatcher stopped. The orchestrator session was not killed."}
