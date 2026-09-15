"""darkarchon-orchestrators — Hermes plugin.

Gives Hermes a fleet-manager role over tmux-based Claude Code orchestrators:

- ``orchestrator`` tool — the model spawns orchestrator sessions, dispatches
  tasks to them (darkarchon file-based protocol), polls run results, inspects
  state, and kills sessions.
- ``/orch`` slash command — read-only fleet/runs overview for the human.

Heavy lifting happens in :mod:`orchestrators`, which shells out to the
darkarchon scripts at ``$DARKARCHON_HOME`` (default ``~/work/darkarchon``).
"""

from __future__ import annotations

import json
from typing import Any, Dict

from . import orchestrators as orch

ORCHESTRATOR_SCHEMA = {
    "name": "orchestrator",
    "description": (
        "Manage your staff of tmux-based Claude Code 'orchestrator' sessions "
        "(darkarchon). Treat each orchestrator as a long-lived EMPLOYEE: it "
        "has a name, a job charter (brief), and its own sub-worker team, and "
        "it stays hired between tasks. Delegate BIG self-contained missions — "
        "not micro-steps. Prefer giving new work to an existing idle employee "
        "whose charter fits over hiring a duplicate.\n\n"
        "TEAMS — EVERY HIRE NEEDS ONE: an employee is hired INTO a team, and "
        "the team is a real darkarchon namespace (~/.darkarchon/<team>/), the "
        "same thing the user gets by making a team by hand. spawn/invite "
        "REFUSE without team=. Never invent the name: if the user did not say "
        "which team ('aaa를 bbb팀으로 채용해줘' → team=bbb), ASK THEM — name "
        "the existing teams (action=teams) and let them pick one or give a "
        "new name — then hire in a later turn. A new name simply creates the "
        "team; no separate setup call is needed.\n\n"
        "NAMES: dispatch/status accept a unique name prefix ('inf' reaches "
        "the one employee starting with 'inf'); resolution spans ALL teams, "
        "so you never need to know an employee's team to reach it. The tool "
        "errors with candidates when ambiguous — relay that, don't guess.\n\n"
        "Actions:\n"
        "- teams: list the teams and their rosters. Use it before asking the "
        "user which team a new hire joins.\n"
        "- set_team: create a team / change the default one without hiring. "
        "Rarely needed — hiring with team=<name> already creates it. Nothing "
        "is hidden by switching: all teams stay visible and dispatchable. "
        "set_fleet is a deprecated alias.\n"
        "- spawn: hire an employee (name + cwd + REQUIRED team + optional "
        "brief = job charter injected into its system prompt). "
        "Each employee gets its OWN tmux "
        "session named after it, where its sub-workers also live. Takes ~15s "
        "to boot; check with status before the first dispatch. When an "
        "employee died (PC reboot, killed session) pass resume=true to "
        "re-hire it with its previous conversation restored — use this "
        "whenever the user asks to bring back / 다시 출근 a former employee "
        "instead of hiring fresh. To hire a NEW employee that simply "
        "CONTINUES whatever Claude work already exists in a directory "
        "(the user says '기존 작업 이어서' / 'continue the work in <dir>'), "
        "pass continue_work=true with that cwd — the latest session there "
        "is found and resumed automatically, no session id needed.\n"
        "- invite: adopt an ALREADY-RUNNING Claude session (target = "
        "'session:window') as an employee instead of spawning a new one; it "
        "needs a team just like spawn. "
        "External employees can be dispatched to but never killed — use "
        "uninvite to let one go (the session itself is left untouched).\n"
        "- uninvite: remove an invited employee from the roster.\n"
        "- dispatch: assign a task. Waits up to wait_seconds (default 120) "
        "for completion; if still running, returns a run_id and a completion "
        "report is INJECTED into this conversation automatically when the "
        "run finishes — tell the user it's running and end your turn instead "
        "of polling. Never dispatch to an employee that already has a "
        "running dispatch.\n"
        "- result: poll a run_id — returns running (+ log tail) or the final "
        "result text.\n"
        "- status: one employee's live state (idle/busy/awaiting_user/...).\n"
        "- list: the whole staff with states.\n"
        "- runs: recent dispatch runs.\n"
        "- questions: pending questions employees escalated to the manager. "
        "Check this whenever a dispatch fails or stalls, and periodically — "
        "questions are silent (no push). Relay them to the user and deliver "
        "the user's decision back with action=answer.\n"
        "- answer: answer a question (question_id + answer text). Only relay "
        "the USER's decision — never invent an answer to a question that was "
        "escalated for a human.\n"
        "- interrupt: stop tracking a run (does not kill the session).\n"
        "- kill: lay an employee off (only when the user asks, or on "
        "throwaway spawns).\n\n"
        "If status shows awaiting_user, the session needs a human to attach "
        "to its tmux window — report that instead of re-dispatching."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["teams", "set_team", "set_fleet", "spawn", "invite",
                         "uninvite", "dispatch",
                         "result", "status", "list", "runs", "questions",
                         "answer", "interrupt", "kill"],
                "description": "Staff operation to perform.",
            },
            "team": {
                "type": "string",
                "description": (
                    "The darkarchon team ([a-zA-Z0-9_-]) — REQUIRED for "
                    "spawn/invite: it is the namespace the employee is hired "
                    "into (~/.darkarchon/<team>/). Use the name the USER "
                    "gave ('X팀으로 채용' → team=X); ask them if they did not "
                    "give one, never invent it. An unknown name creates the "
                    "team. Also the target of set_team."
                ),
            },
            "fleet": {
                "type": "string",
                "description": "Deprecated alias for team (set_fleet only).",
            },
            "force": {
                "type": "boolean",
                "description": "Accepted but unused (legacy set_fleet flag).",
            },
            "name": {
                "type": "string",
                "description": (
                    "Employee name ([a-zA-Z0-9_-]). Required for spawn, "
                    "dispatch, status, kill. Pick role-like names the user "
                    "will recognize (e.g. backend-dev, docs-writer)."
                ),
            },
            "cwd": {
                "type": "string",
                "description": (
                    "Working directory for the new session (spawn only). "
                    "Usually the repo the employee will primarily own."
                ),
            },
            "target": {
                "type": "string",
                "description": (
                    "invite only: tmux location of the existing Claude "
                    "session, as 'session:window' (e.g. 'sec-orch:1')."
                ),
            },
            "brief": {
                "type": "string",
                "description": (
                    "spawn only, recommended: the employee's job charter — "
                    "who they are, what they own, quality standards, "
                    "constraints. Injected permanently into their system "
                    "prompt, so it shapes every future task."
                ),
            },
            "resume": {
                "type": "boolean",
                "description": (
                    "spawn only: re-hire a DEAD employee with its previous "
                    "Claude conversation restored (recorded session id + "
                    "claude --resume). Refused if the employee is alive or "
                    "has no recorded session. Its sub-workers are reset."
                ),
            },
            "session_id": {
                "type": "string",
                "description": (
                    "spawn only: hire a NEW employee that CONTINUES an "
                    "existing Claude conversation with this session id "
                    "(e.g. promoting the user's own past session — they get "
                    "it from /status inside that session). cwd must match "
                    "the session's original cwd, and the original session "
                    "should be closed first. Do not combine with resume. "
                    "Prefer continue_work when you don't have a specific id."
                ),
            },
            "continue_work": {
                "type": "boolean",
                "description": (
                    "spawn only: hire a NEW employee that continues the "
                    "MOST RECENT Claude session in its cwd — the session id "
                    "is discovered automatically (no /status needed). Use "
                    "when the user just wants to pick up existing work in a "
                    "directory. Close that session first if it is still open."
                ),
            },
            "task": {
                "type": "string",
                "description": (
                    "Full task description (dispatch only). Self-contained "
                    "mission brief: goal, constraints, definition of done. "
                    "No length limit — it is delivered via file, not typed."
                ),
            },
            "run_id": {
                "type": "string",
                "description": "Run identifier (result / interrupt).",
            },
            "question_id": {
                "type": "string",
                "description": "Question identifier (answer only).",
            },
            "answer": {
                "type": "string",
                "description": (
                    "answer only: the user's decision, relayed verbatim or "
                    "faithfully summarized."
                ),
            },
            "wait_seconds": {
                "type": "integer",
                "description": (
                    "dispatch only: seconds to wait inline for completion "
                    "before returning a pollable run_id. Default 120, max 540. "
                    "Use 0 for fire-and-poll."
                ),
            },
        },
        "required": ["action"],
    },
}


def _handle_tool(args: Dict[str, Any], **_kw: Any) -> str:
    action = (args.get("action") or "").strip()
    name = (args.get("name") or "").strip()
    run_id = (args.get("run_id") or "").strip()
    try:
        if action == "teams":
            out = orch.teams()
        elif action in ("set_team", "set_fleet"):
            out = orch.set_team(args.get("team") or args.get("fleet") or "",
                                bool(args.get("force")))
        elif action == "spawn":
            out = orch.spawn(name, (args.get("cwd") or "").strip(),
                             args.get("brief") or "",
                             bool(args.get("resume")),
                             args.get("session_id") or "",
                             bool(args.get("continue_work")),
                             args.get("team") or "")
        elif action == "invite":
            out = orch.invite(name, args.get("target") or "",
                              args.get("team") or "")
        elif action == "uninvite":
            out = orch.uninvite(name)
        elif action == "dispatch":
            out = orch.dispatch(name, args.get("task") or "",
                                args.get("wait_seconds", 120))
        elif action == "result":
            out = orch.result(run_id)
        elif action == "status":
            out = orch.status(name)
        elif action == "list":
            out = orch.list_orchestrators()
        elif action == "runs":
            out = orch.runs()
        elif action == "questions":
            out = orch.questions()
        elif action == "answer":
            out = orch.answer(args.get("question_id") or "",
                              args.get("answer") or "")
        elif action == "interrupt":
            out = orch.interrupt(run_id)
        elif action == "kill":
            out = orch.kill(name)
        else:
            out = {"ok": False, "error": f"unknown action '{action}'"}
    except Exception as exc:  # surface, never crash the agent loop
        out = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return json.dumps(out, ensure_ascii=False)


_HELP = """\
/orch — darkarchon orchestrator staff overview

Subcommands:
  list            Employees + live state, grouped by team (default)
  runs            Recent dispatch runs
  questions       Pending questions escalated by employees
  team [<name>]   Show the teams, or make <name> the default one
  help            This text

Hiring/dispatching is done by the agent via the `orchestrator` tool —
just ask in chat, e.g. "hire a backend-dev on ~/work/foo into the api team
and have it ...". Every hire names the team it joins.
Quick dispatch without the model in the loop:  /to <employee> <task>
(a unique name prefix works: /to inf ...)
"""


def _handle_slash(raw_args: str) -> str:
    sub = (raw_args or "").strip().split()
    cmd = sub[0] if sub else "list"
    if cmd in {"help", "-h", "--help"}:
        return _HELP
    if cmd in ("team", "teams", "fleet"):
        if len(sub) > 1:
            out = orch.set_team(sub[1])
            return out.get("note") if out.get("ok") else f"Error: {out.get('error')}"
        data = orch.teams()
        if not data["teams"]:
            return ("No teams yet — the first hire creates one "
                    "(ask me to hire someone into a team).")
        lines = ["Teams:"]
        for t in data["teams"]:
            roster = ", ".join(t["employees"]) or "(empty)"
            lines.append(f"  {t['team']}{' *' if t['current'] else ''}"
                         f"  {roster}")
        lines.append("(* = most recent team; every hire still names its own)")
        return "\n".join(lines)
    if cmd == "questions":
        data = orch.questions()
        if not data.get("ok"):
            return f"Error: {data.get('error')}"
        if not data["questions"]:
            return "No pending questions."
        lines = ["Pending questions (answer via chat — I'll relay):"]
        for q in data["questions"]:
            body = (q.get("body") or "").replace("\n", " ")[:100]
            lines.append(f"  {q['question_id']}  [{q['from_worker']}] {body}")
        return "\n".join(lines)
    if cmd == "runs":
        data = orch.runs()
        if not data.get("ok"):
            return f"Error: {data.get('error')}"
        if not data["runs"]:
            return "No dispatch runs yet."
        lines = ["Recent runs:"]
        for r in data["runs"]:
            lines.append(
                f"  {r['run_id']}  [{r['status']:<9}] {r['orchestrator']}: "
                f"{(r.get('task_preview') or '').splitlines()[0][:60]}"
            )
        return "\n".join(lines)
    if cmd == "list":
        data = orch.list_orchestrators()
        if not data.get("ok"):
            return f"Error: {data.get('error')}"
        if not data["orchestrators"]:
            return "No employees hired yet. Ask me to hire one into a team."
        lines = ["Staff:"]
        by_team: Dict[str, list] = {}
        for o in data["orchestrators"]:
            by_team.setdefault(o.get("team") or "", []).append(o)
        for label in sorted(by_team, key=lambda t: (t == "", t)):
            if len(by_team) > 1 or label:
                lines.append(f"  [{label or 'no team'}]")
            for o in by_team[label]:
                detail = f" — {o['detail']}" if o.get("detail") else ""
                lines.append(f"    {o['name']:<20} {o['state']:<14} "
                             f"({o['target']}){detail}")
        return "\n".join(lines)
    return f"Unknown subcommand: {cmd}\n\n{_HELP}"


def _handle_to(raw_args: str) -> str:
    """/to <employee> <task...> — deterministic dispatch, no LLM in the loop."""
    parts = (raw_args or "").strip().split(None, 1)
    if len(parts) < 2:
        roster = ", ".join(o["name"] for o in
                           (orch.list_orchestrators().get("orchestrators") or []))
        return (f"Usage: /to <employee> <task>\n"
                f"Employees: {roster or '(none — hire one first)'}")
    query, task = parts[0], parts[1]
    name, _team, cands = orch._resolve_name(query)
    if name is None:
        if cands:
            return (f"'{query.lstrip('@')}' is ambiguous — did you mean: "
                    f"{', '.join(cands)}?")
        roster = ", ".join(o["name"] for o in
                           (orch.list_orchestrators().get("orchestrators") or []))
        return (f"No employee matches '{query.lstrip('@')}'. "
                f"Employees: {roster or '(none — hire one first)'}")

    st = orch.status(name)
    if not st.get("ok"):
        return f"Error: {st.get('error')}"
    state = st.get("state")
    if state in ("dead", "unknown"):
        return (f"'{name}' is {state} — revive it first "
                f"(e.g. tell me to bring it back with resume).")
    if state in ("busy", "compacting"):
        return (f"'{name}' is {state} right now — one dispatch at a time. "
                f"Try again when it finishes.")
    if state in ("awaiting_user", "awaiting_permission"):
        return (f"'{name}' is waiting for input in its pane "
                f"(tmux attach -t {name}) — answer that first.")

    r = orch.dispatch(name, task, wait_seconds=0)
    if not r.get("ok"):
        return f"Error: {r.get('error')}{': ' + r.get('detail', '') if r.get('detail') else ''}"
    return (f"→ {name}: dispatched (run {r.get('run_id')}). "
            f"You'll be notified when it finishes.")


def register(ctx) -> None:
    orch.set_context(ctx)  # enables completion push via ctx.inject_message
    ctx.register_command(
        "to",
        handler=_handle_to,
        description="Dispatch a task straight to an employee: /to <name> <task>",
        args_hint="<employee> <task>",
    )
    ctx.register_tool(
        name="orchestrator",
        toolset="orchestrator",
        schema=ORCHESTRATOR_SCHEMA,
        handler=_handle_tool,
        description="Spawn/dispatch/manage tmux Claude Code orchestrators (darkarchon).",
        emoji="🎛️",
    )
    ctx.register_command(
        "orch",
        handler=_handle_slash,
        description="darkarchon orchestrator fleet overview (list/runs).",
        args_hint="[list|runs]",
    )
