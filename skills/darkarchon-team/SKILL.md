---
name: darkarchon-team
description: Use when the user wants to coordinate multiple coding-agent workers (Claude Code or OpenAI Codex) across separate tmux windows/repos with darkarchon — spawning or inviting workers, dispatching tasks ("have X build/review Y"), or starting/stopping/inspecting the team. Triggers on intents like "spawn a worker", "invite this pane to the team", "dispatch to X", "start/stop the team".
---

# darkarchon team

darkarchon coordinates multiple coding-agent CLIs — each worker is an independent
`claude` or `codex` process living in its own tmux window, with its own cwd and
config. This skill maps natural-language requests to darkarchon commands so you
(the orchestrator) don't have to memorize the shell flags.

- Scripts live at **`$DARKARCHON_HOME`** — set this in your shell rc, e.g.
  `export DARKARCHON_HOME=~/work/darkarchon`.
- This is a **generic, copy-me example**. Drop it in `~/.claude/skills/` and adapt
  the team-naming / project conventions to your own setup.

## Team name (`DARKARCHON_TEAM`)

darkarchon isolates tmux sessions and state by one env var, `DARKARCHON_TEAM`.
Workers in different teams are fully separate (own tmux session + own
`~/.darkarchon/<team>/` state dir).

- Pick **one team name per workspace/project** and reuse it for the whole
  conversation. Unset ⇒ the `default` team.
- If the user hasn't said which team, ask once (suggest `default` or a
  project-based name), then prefix **every** command below with
  `DARKARCHON_TEAM=<team>`.

## Choosing the worker kind (Claude vs Codex) — spawn only

When **you launch** a worker you must pick the agent. When **inviting** an
existing pane, the kind is auto-detected (skip this).

1. If the user names it ("as codex", "a claude worker"), pass `--kind` accordingly.
2. Otherwise detect installed CLIs — `command -v claude` / `command -v codex`:
   - Only **one** installed → use it, no question.
   - **Both** installed → ask the user once which to use; reuse that choice for
     the rest of the session.
3. For codex, make sure `codex login` is done (or `OPENAI_API_KEY` is set) first,
   or every turn 401s.

## Command map

Prefix each command with `DARKARCHON_TEAM=<team>`.

**Spawn a new worker** (darkarchon launches the agent):
```bash
$DARKARCHON_HOME/lib/spawn-worker.sh [--kind claude|codex] <name> <cwd> [<role>]
```
- Creates a tmux window named `<name>` in session `<team>` (auto-creates the
  session) and starts the agent in `<cwd>`.
- Wait ~10–15s. If a trust prompt appears, the user must attach and press Enter:
  `tmux attach -t <team>:<name>`.

**Invite an already-running pane** (no respawn):
```bash
$DARKARCHON_HOME/invite-worker.sh [--kind claude|codex] <name> <session:window> [<role>]
```
- Registers a pane you already have running. Kind is auto-detected from the pane;
  pass `--kind` only to override. Marked EXTERNAL, so `kill-worker.sh` refuses it.
- Targets a **window**, not a pane — two agents must be in **separate windows**.

**Dispatch a task / ask a worker:**
```bash
$DARKARCHON_HOME/dispatch-safe.sh [--force] <name> '<task description>'
```
- Refuses (non-zero exit) when the worker is busy, when another worker
  **sharing its cwd** is busy (same-repo dispatches are serialized so two
  agents don't edit the working tree at once), or when the prompt line looks
  like it has unsent user input. Retry once it's idle.
- **`--force`**: when the "unsent input" refusal is actually a Claude Code
  recap-suggestion or autocomplete ghost text (not a real user keystroke),
  pass `--force` to wipe the prompt line (BSpace burst) and dispatch anyway.
  Real user-typed text would be clobbered — only force when you've ruled that
  out. If the user is actively chatting with the worker, ask them to detach
  first instead.
- The result is printed to stdout when the worker finishes.
- tmux's `capture-pane` can lag the live TUI by several seconds — wait 1–2s
  before retrying keystrokes if a previous send seems to have had no effect.

**Remove / stop:**
```bash
$DARKARCHON_HOME/uninvite-worker.sh <name>     # unregister an invited worker (pane untouched)
$DARKARCHON_HOME/lib/kill-worker.sh <name>     # close a spawned worker's window (refuses EXTERNAL)
$DARKARCHON_HOME/lib/start.sh                  # spawn the fixed roster from config.env WORKERS=()
$DARKARCHON_HOME/lib/stop.sh                   # kill the team's tmux session
```

**Inspect history:**
```bash
$DARKARCHON_HOME/lib/tasks.sh list | today | failed | show <id> | result <id>
```

**Dashboard** (optional; one hub serves all teams):
```bash
$DARKARCHON_HOME/dashboard.sh start        # JSON API hub
$DARKARCHON_HOME/dashboard-ui.sh start     # web UI (Vite) on http://localhost:5173
```

## Conventions

- The worker **name** is your handle for dispatch — keep it stable and unique
  within a team.
- Don't dispatch to the **same** worker twice concurrently (no internal lock);
  different workers run in parallel fine.
- A Claude "dev" worker and a Codex "reviewer" can share one repo: dispatches to
  the shared cwd serialize automatically. Tell the reviewer to be **read-only**
  (codex workers run with broad write access).
- Internals and multi-host setup: see `$DARKARCHON_HOME/README.md`.
