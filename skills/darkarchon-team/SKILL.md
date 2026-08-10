---
name: darkarchon-team
description: Use when the user's message says "darkarchon" — spawn or invite workers, dispatch tasks across repos, check worker state, remove/revive workers, read history, run the dashboard. darkarchon runs each worker as its own claude or codex CLI process in a separate tmux window with its own cwd, config and MCP servers. Do NOT use this for Claude Code's built-in agent teams, subagents, or the Task tool — a bare "team", "agent team" or "subagent" request means that other system, not this one. Once darkarchon context is active in a conversation, follow-ups ("kill X", "ask X to review Y", "who's idle") route here too.
---

# darkarchon

Coordinate multiple coding-agent CLIs across separate tmux windows and repos.
Each worker is an independent `claude` or `codex` process with its own cwd,
skills, plugins and MCP servers — unlike Claude Code subagents, which share the
parent's cwd and config.

## Trigger rule

**The word "darkarchon" is the gate.** Claude Code has its own agent-team and
subagent features, so a bare "team" / "agent" / "spawn an agent" request belongs
to those, not here. Route to this skill when the user writes "darkarchon", and
keep routing here for follow-ups in the same conversation once that context is
active.

## Before running anything

**1. Resolve `$DARKARCHON_HOME`** (the repo root). Normally exported from the
shell rc by `install.sh`. If unset, resolve it rather than failing — this skill
is a symlink into the repo, so `readlink -f ~/.claude/skills/darkarchon-team`
gives `<repo>/skills/darkarchon-team`, and the repo is two directories up.
`~/work/darkarchon` is the default clone location.

**2. Resolve the team** (`DARKARCHON_TEAM`). One env var isolates everything:
each team gets its own tmux session and its own `~/.darkarchon/<team>/` state
dir. Workers in different teams cannot see each other.

- Already decided in this conversation, or named by the user → use it.
- Otherwise list what exists: `ls ~/.darkarchon/` (each subdir is a team).
  - Exactly one → use it, and say which one you picked.
  - Several → ask which.
  - None → ask for a name (`default`, or one based on the project/branch).
    There is no "create team" command; a team exists once something is spawned
    or invited into it.

**Prefix every command below with `DARKARCHON_TEAM=<team>`.** Forgetting it
silently targets the `default` team — the command succeeds against the wrong
roster, which is worse than an error.

## Add workers

**Spawn** — darkarchon launches the agent:

```bash
$DARKARCHON_HOME/lib/spawn-worker.sh [--kind claude|codex] [--env K=V] <name> <cwd> [<role>]
```

Creates a tmux window named `<name>` in session `<team>` (auto-creating the
session) and starts the agent in `<cwd>`. Wait ~10–15s. If a trust prompt
appears, it must be answered before the worker is usable —
`tmux attach -t <team>:<name>`, or send the keypress yourself if the user has
authorized acting in that pane.

`--env` (repeatable) prepends env assignments to the launcher, so the agent and
every Bash it runs inherit them. Main use: giving an orchestrator-role worker
its own `DARKARCHON_TEAM` for the sub-team it manages.

**Choosing the kind (spawn only — invite auto-detects):**

1. User said which ("as codex", "a claude worker") → pass `--kind`.
2. Otherwise check `command -v claude` / `command -v codex`:
   - Only one installed → use it silently.
   - Both installed → **ask on every spawn.** Workers are often deliberately
     mixed (a claude dev + a codex reviewer), so never remember one answer as a
     default for the next worker.
3. codex needs `codex login` (or `OPENAI_API_KEY`) first, or every turn 401s.

**Invite** — register a pane the user already has running:

```bash
$DARKARCHON_HOME/invite-worker.sh [--kind claude|codex] <name> <session:window> [<role>]
```

Kind is auto-detected from pane content; pass `--kind` only to correct it. cwd
is derived from the pane. Targets a **window**, not a pane — two agents must
live in separate windows. Marked `EXTERNAL`, so `kill-worker.sh` refuses it:
the user owns that process, not us.

**Build a team from existing panes** ("set up a darkarchon team"):

```bash
tmux list-windows -a -F '#{session_name}:#{window_index}  #{window_name}  #{pane_current_command}  #{pane_current_path}'
```

Present the windows that look like coding agents, ask which to invite and under
what names (suggest each pane's cwd basename), then invite each.

## Give work

```bash
$DARKARCHON_HOME/dispatch-safe.sh [--force] [--after <id>[,<id>...]] <name> '<task>'
```

The normal way to dispatch. Blocks until the worker answers, then prints the
result to stdout. It refuses rather than firing blindly when:

| Refusal | Meaning | What to do |
|---|---|---|
| busy / compacting / rate-limited | mid-turn | retry when idle, or `wait-worker.sh` |
| awaiting permission / user input | needs the human | tell the user to answer in that pane |
| unsent input on the prompt line | someone typed there | see `--force` below |
| codex auth error | not logged in | `codex login` |
| a *different* worker sharing its cwd is busy | same-repo edits are serialized | wait for that one |

`--after <id>` records a dependency and waits for those task ids first.

**`--force`** wipes the prompt line before dispatching. Only use it when the
"unsent input" is Claude Code ghost text (a recap suggestion or autocomplete),
never when the user may have typed something real — it clobbers their keystrokes.
If they're actively chatting with that worker, ask them to detach instead.

Raw dispatch with no safety checks (rarely what you want):

```bash
$DARKARCHON_HOME/lib/dispatch.sh <name> '<task>'
echo 'long prompt' | $DARKARCHON_HOME/lib/dispatch.sh <name> -
```

**Don't dispatch to the same worker twice concurrently** — there is no internal
lock across separate invocations of the raw script. Different workers run in
parallel fine.

## Check state / wait

```bash
$DARKARCHON_HOME/check-worker-state.sh <name> [--verbose]
$DARKARCHON_HOME/wait-worker.sh <name> [--until s1,s2] [--timeout SEC] [--interval SEC] [--json]
```

`check-worker-state.sh` prints one `KEY=VALUE` line: `state=`, `source=`
(`hook` beats `scrape`), and `task=` if a dispatch is running. States: `idle`
`busy` `compacting` `awaiting_permission` `awaiting_user` `unsent`
`rate_limited` `error` `dead` `unknown`.

`wait-worker.sh` blocks until the worker settles — by default idle **or**
needing a human, so a permission prompt surfaces immediately instead of looking
busy until timeout. Exits `0` reached, `1` unknown worker, `2` timeout, `3` died.

`orphaned=1` means someone relaunched their own agent in that window. Treat it
as a stop sign: never `kill-worker.sh` an orphaned worker.

## Remove a worker — ask which one they mean

"Remove", "stop", "kill", "get rid of X" map to **three different commands** with
very different blast radii. When the request is ambiguous, prefer the
non-destructive reading or ask — the pane may hold a live session the user is
working in.

```bash
$DARKARCHON_HOME/lib/deregister-worker.sh <name> [--force]  # unregister ANY worker, pane untouched
$DARKARCHON_HOME/uninvite-worker.sh <name>                  # unregister an INVITED worker, pane untouched
$DARKARCHON_HOME/lib/kill-worker.sh <name>                  # DESTRUCTIVE: closes the window, ends the process
$DARKARCHON_HOME/prune-workers.sh [--dry-run|--yes]         # drop every DEAD registration, never touches a window
$DARKARCHON_HOME/lib/stop.sh                                # kill the whole team's tmux session
```

- "take X off the team", "X is no longer needed" → `deregister-worker.sh`
  (refuses a live worker unless `--force`).
- "kill X", "shut X down", "close X's window" → `kill-worker.sh`. Refuses
  EXTERNAL workers by design. Before running it: check state, confirm it isn't
  `orphaned`, and note that a busy worker loses its work.
- "clean up the dead ones" → `prune-workers.sh --dry-run` first.

Killing or deregistering writes a tombstone (`departed/<name>.json`) holding
cwd, role, kind and Claude session id — so removal is recoverable via revive.

## Bring a worker back

```bash
$DARKARCHON_HOME/revive-worker.sh <name>                    # new window, conversation restored (claude --resume)
$DARKARCHON_HOME/revive-worker.sh <name> --fresh            # clean start; picks up the handover note
$DARKARCHON_HOME/revive-worker.sh <name> --session-id <id>  # resume a specific session
$DARKARCHON_HOME/revive-worker.sh <name> --adopt            # register the agent already in its old pane
$DARKARCHON_HOME/revive-worker.sh <name> --dry-run          # print the plan only
```

- Use `--fresh` when the worker was killed **because its context filled up** —
  `--resume` replays that full context and it hits the wall again immediately.
  Plain revive is for reboots and accidental kills.
- The old window is preserved, renamed `<name>-old`.
- A revived worker is a new process, so charter, hooks, heartbeat and MCP tools
  are reattached. A pane the user relaunched by hand has none of those, which is
  why `--adopt` is the degraded option, not the default.
- `dead` means "nobody answers to that name" — not that the window is empty.

## Messages and questions

Orchestrator-side peer messaging (workers use the `mailbox_send` MCP tool):

```bash
$DARKARCHON_HOME/lib/mailbox.sh send <to> [--from <who>] <body>
$DARKARCHON_HOME/lib/mailbox.sh read <name>          # drain (destructive)
$DARKARCHON_HOME/lib/mailbox.sh peek|count <name>
$DARKARCHON_HOME/lib/mailbox.sh outstanding <name> [--older-than SEC]
$DARKARCHON_HOME/lib/mailbox.sh renotify <name>
$DARKARCHON_HOME/lib/mailbox.sh clear <name>
```

`<to>` is a worker name or a group: `@all`, `@idle`, `@claude`, `@codex`,
`@cwd:<dir>`. The sender is never included in its own group send. Group
addresses must be written literally with `@`. `outstanding` + `renotify` is the
recovery path when a worker never picked a message up.

Answering questions workers filed with `ask`:

```bash
$DARKARCHON_HOME/questions.sh list [--all]
$DARKARCHON_HOME/questions.sh show <id>
$DARKARCHON_HOME/questions.sh answer <id> "<answer>"     # delivered via the worker's mailbox
$DARKARCHON_HOME/questions.sh dismiss <id> ["<reason>"]
$DARKARCHON_HOME/questions.sh clear-answered
```

## History

```bash
$DARKARCHON_HOME/lib/tasks.sh list | today | failed | show <id> | result <id> | prune-old [DAYS]
```

## Teams

```bash
$DARKARCHON_HOME/lib/teams.sh list [--json]                    # every team graded by last activity
$DARKARCHON_HOME/lib/teams.sh archive <team>... | --stale | --inactive
$DARKARCHON_HOME/lib/start.sh                                  # spawn the fixed roster in config.env WORKERS=()
```

Archiving **moves** a state dir to `~/.darkarchon-archive/<date>/` — it never
deletes, and refuses a team whose tmux session is still alive. Restore by moving
the directory back.

## Dashboard and monitoring (optional)

```bash
$DARKARCHON_HOME/agent.sh start|stop|status          # per-HOST scanner (one per machine, not per team)
$DARKARCHON_HOME/dashboard.sh start [port]|stop|status  # hub; port derives from the team name
$DARKARCHON_HOME/dashboard-ui.sh start|stop|status      # web UI on http://localhost:5173
$DARKARCHON_HOME/notify-watcher.sh [hub_url]            # macOS desktop notifications from hub SSE
```

## Worker-side commands (for reference)

Workers normally use MCP tools (`mcp__darkarchon__ask`, `mailbox_send`,
`mailbox_drain`, `status_get`). The shell equivalents write the same files:

```bash
$DARKARCHON_HOME/lib/ask.sh [--from <worker>] [--blocking [--timeout SEC]] "<question>"
$DARKARCHON_HOME/lib/leave-team.sh --reason context-full --handover -   # a worker resigns
```

A worker running out of context should resign rather than die: `leave-team.sh`
deregisters it, records where to find it, and leaves a handover note that
`revive-worker.sh <name> --fresh` gives to its replacement.

## Install / update

```bash
$DARKARCHON_HOME/install.sh
```

Idempotent. Symlinks `~/.claude/skills/darkarchon-team` → this skill (so
`git pull` updates it in place; a real file already there is backed up, never
deleted) and appends `DARKARCHON_HOME` to the shell rc. Everything else
(`pip install mcp`, dashboard npm) is optional — see the README.

## Conventions

- The worker **name** is the handle for every command — keep it stable and
  unique within a team. Repo basename is the usual choice.
- A claude "dev" and a codex "reviewer" can share one repo: dispatches to the
  same cwd serialize automatically. Tell the reviewer to stay read-only (codex
  workers run with broad write access).
- `tmux capture-pane` can lag the live TUI by seconds. Wait 1–2s before
  retrying a keystroke that appears to have had no effect.
- Internals, multi-host setup and the state-resolver design: `$DARKARCHON_HOME/README.md`.
