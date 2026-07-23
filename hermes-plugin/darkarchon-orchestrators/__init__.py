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
        "Actions:\n"
        "- set_team: set the fleet session name (once, from the user's answer).\n"
        "- spawn: hire an employee (name + cwd + optional brief = job charter "
        "injected into its system prompt). Takes ~15s to boot; check with "
        "status before the first dispatch.\n"
        "- dispatch: assign a task. Waits up to wait_seconds (default 120) for "
        "completion; if still running, returns a run_id to poll. Never "
        "dispatch to an employee that already has a running dispatch.\n"
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
                "enum": ["set_team", "spawn", "dispatch", "result", "status",
                         "list", "runs", "questions", "answer", "interrupt",
                         "kill"],
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
            "brief": {
                "type": "string",
                "description": (
                    "spawn only, recommended: the employee's job charter — "
                    "who they are, what they own, quality standards, "
                    "constraints. Injected permanently into their system "
                    "prompt, so it shapes every future task."
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
                             args.get("brief") or "")
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


def register(ctx) -> None:
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
