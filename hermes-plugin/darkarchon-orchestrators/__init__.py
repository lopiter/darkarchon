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
        "FIRST USE: the fleet's tmux session name is not preset. If any action "
        "returns the 'No fleet session name' error, ask the USER what to name "
        "it (never invent one), then call action=set_team.\n\n"
        "MENTIONS: when the user's message starts with '@<name-or-prefix>', "
        "everything after the mention is a task for that employee — call "
        "action=dispatch with name=<the mention, @ stripped> and the rest as "
        "the task, verbatim. A unique prefix is fine ('@inf ...' reaches the "
        "one employee starting with 'inf'); the tool resolves it and errors "
        "with candidates when ambiguous — relay that error, don't guess. "
        "Example: '@voc fix the login bug' → dispatch(name='voc', "
        "task='fix the login bug'). Do not reinterpret or expand it.\n\n"
        "Actions:\n"
        "- set_team: set the fleet session name (once, from the user's answer).\n"
        "- spawn: hire an employee (name + cwd + optional brief = job charter "
        "injected into its system prompt). Each employee gets its OWN tmux "
        "session named after it, where its sub-workers also live. Takes ~15s "
        "to boot; check with status before the first dispatch. When an "
        "employee died (PC reboot, killed session) pass resume=true to "
        "re-hire it with its previous conversation restored — use this "
        "whenever the user asks to bring back / 다시 출근 a former employee "
        "instead of hiring fresh.\n"
        "- invite: adopt an ALREADY-RUNNING Claude session (target = "
        "'session:window') as an employee instead of spawning a new one. "
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
                "enum": ["set_team", "spawn", "invite", "uninvite", "dispatch",
                         "result", "status", "list", "runs", "questions",
                         "answer", "interrupt", "kill"],
                "description": "Fleet operation to perform.",
            },
            "team": {
                "type": "string",
                "description": (
                    "set_team only: fleet tmux session name, as chosen by the "
                    "user ([a-zA-Z0-9_-])."
                ),
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
                    "should be closed first. Do not combine with resume."
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
        if action == "set_team":
            out = orch.set_team(args.get("team") or "")
        elif action == "spawn":
            out = orch.spawn(name, (args.get("cwd") or "").strip(),
                             args.get("brief") or "",
                             bool(args.get("resume")),
                             args.get("session_id") or "")
        elif action == "invite":
            out = orch.invite(name, args.get("target") or "")
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
  list            Employees + live state (default)
  runs            Recent dispatch runs
  questions       Pending questions escalated by employees
  team [<name>]   Show or set the fleet tmux session name
  help            This text

Hiring/dispatching is done by the agent via the `orchestrator` tool —
just ask in chat, e.g. "hire a backend-dev on ~/work/foo and have it ...".
Quick dispatch without the model in the loop:  /to <employee> <task>
(or start a chat message with @<employee> — the agent routes the rest to them.)
"""


def _handle_slash(raw_args: str) -> str:
    sub = (raw_args or "").strip().split()
    cmd = sub[0] if sub else "list"
    if cmd in {"help", "-h", "--help"}:
        return _HELP
    if cmd == "team":
        if len(sub) > 1:
            out = orch.set_team(sub[1])
            return out.get("note") if out.get("ok") else f"Error: {out.get('error')}"
        team = orch.manager_team()
        return (f"Fleet session: '{team}'" if team
                else "No fleet session name set yet. Use `/orch team <name>`.")
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
            return (f"No employees in team '{data['team']}' yet. "
                    f"Ask me to hire one.")
        lines = [f"Staff (team '{data['team']}'):"]
        for o in data["orchestrators"]:
            detail = f" — {o['detail']}" if o.get("detail") else ""
            lines.append(f"  {o['name']:<20} {o['state']:<14} "
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
    name, cands = orch._resolve_name(query)
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
    if state == "awaiting_user":
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
