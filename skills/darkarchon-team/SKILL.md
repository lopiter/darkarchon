---
name: darkarchon-team
description: Use when the user's message mentions the "dark team" — e.g. "create a dark team", "invite session:window to the dark team", "invite to the dark team" — to coordinate multiple coding-agent workers (Claude Code or OpenAI Codex) across separate tmux windows/repos with darkarchon. Do NOT trigger on bare "team" requests; those belong to Claude Code's native team / sub-agent features. Also use for follow-up commands ("have X build/review Y", "stop the team") in a conversation where the dark team context is already active.
---

# darkarchon team

darkarchon coordinates multiple coding-agent CLIs — each worker is an independent
`claude` or `codex` process living in its own tmux window, with its own cwd and
config. This skill maps natural-language requests to darkarchon commands so you
(the orchestrator) don't have to memorize the shell flags.

- Scripts live at **`$DARKARCHON_HOME`** — set this in your shell rc, e.g.
  `export DARKARCHON_HOME=~/work/darkarchon`.
- If `$DARKARCHON_HOME` is unset (e.g. fresh shell right after install), resolve
  it before failing: this skill is normally a symlink into the repo
  (`readlink -f ~/.claude/skills/darkarchon-team` → `<repo>/skills/darkarchon-team`,
  so the repo is two directories up), and `~/work/darkarchon` is the default
  clone location. Use the resolved path as an inline prefix for the commands below.
- Installed by the repo's `install.sh` as a symlink, so `git pull` keeps it
  current. To customize the team-naming / project conventions, replace the
  symlink with a real copy under a different name and edit that.

## Trigger rule (important)

- **"dark team"** in the user's message is the gate. A bare "team" is Claude
  Code's native team / `Task` sub-agents — a different system; don't route it
  here.
- Once the dark-team context is active in a conversation, follow-ups ("have X
  do Y", "kick X out", "stop the team") route here without repeating the phrase.
- "dark" is the trigger word, **not** the team name — the actual
  `DARKARCHON_TEAM` value is resolved below.

## Team name (`DARKARCHON_TEAM`)

darkarchon isolates tmux sessions and state by one env var, `DARKARCHON_TEAM`.
Workers in different teams are fully separate (own tmux session + own
`~/.darkarchon/<team>/` state dir).

Resolve the team once per conversation, then prefix **every** command below
with `DARKARCHON_TEAM=<team>`:

1. Already decided in this conversation, or named by the user → use it.
2. Otherwise list existing teams: `ls ~/.darkarchon/` (each subdir is a team's
   state dir).
   - Exactly one team exists → use it (mention which one you picked).
   - Several exist → ask which.
   - None exist → ask for a name (suggest `default` or a project-based one).
     There is no separate "create team" command — the team comes into being
     with its first invite/spawn.

## Entry flows

**"Create a dark team"** (e.g. "create a dark team") — build a team from panes
the user already has open:

1. Survey what's running:
   ```bash
   tmux list-windows -a -F '#{session_name}:#{window_index}  #{window_name}  #{pane_current_command}  #{pane_current_path}'
   ```
2. Pick out windows that look like coding agents (`claude` / `codex` /
   `node` running a CLI) and present them, then **ask the user** which windows
   to invite and under what worker names (suggest the basename of each pane's
   cwd as the name). Ask for the team name in the same round if unresolved.
3. Invite each selected window with `invite-worker.sh` (below). If the user
   wants agents that aren't running yet, spawn those with `spawn-worker.sh`.

**"Invite `<session:window>` to the dark team"** — register one existing pane:

1. Resolve the team (see above): if no team exists yet, ask for a name first;
   if one already exists, invite into it without asking.
2. Run `invite-worker.sh` (below) with the target window.

## Choosing the worker kind (Claude vs Codex) — spawn only

When **you launch** a worker you must pick the agent. When **inviting** an
existing pane, the kind is auto-detected (skip this).

1. If the user names it ("as codex", "a claude worker"), pass `--kind` accordingly.
2. Otherwise detect installed CLIs — `command -v claude` / `command -v codex`:
   - Only **one** installed → use it, no question.
   - **Both** installed → ask which to use **on every spawn**. Different workers
     are often different kinds (e.g. a codex reviewer + a claude dev), so do NOT
     remember one answer as a default and reuse it for the next worker.
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
$DARKARCHON_HOME/uninvite-worker.sh <name>       # unregister an invited worker (pane untouched)
$DARKARCHON_HOME/lib/deregister-worker.sh <name> # unregister ANY worker, pane untouched (refuses a live one)
$DARKARCHON_HOME/lib/kill-worker.sh <name>       # close a spawned worker's window (refuses EXTERNAL)
$DARKARCHON_HOME/prune-workers.sh                # drop every dead registration (never kills a window)
$DARKARCHON_HOME/lib/start.sh                    # spawn the fixed roster from config.env WORKERS=()
$DARKARCHON_HOME/lib/stop.sh                     # kill the team's tmux session
```

**Bring a dead worker back** ("다시 불러줘", "복구해줘", worker shows `dead`):
```bash
$DARKARCHON_HOME/revive-worker.sh <name>           # respawn in a NEW window with its conversation (claude --resume)
$DARKARCHON_HOME/revive-worker.sh <name> --fresh   # clean replacement; reads the departing worker's handover note
$DARKARCHON_HOME/revive-worker.sh <name> --adopt   # register the agent already running in its old pane
$DARKARCHON_HOME/revive-worker.sh <name> --dry-run # show the plan first
```
- Pick `--fresh` when the worker was killed **because its context filled up** —
  `--resume` restores that full context and it hits the wall again immediately.
  Plain revive is for reboots and accidental kills.
- If `check-worker-state.sh` reports `orphaned=1`, someone relaunched their own
  claude in that window. **Never** `kill-worker.sh` it — that destroys a live
  session the user is working in. Revive into a new window, or `--adopt` it.
- A revived worker is a new process: charter, hooks, heartbeat and MCP tools are
  reattached. A pane the user relaunched by hand has none of those, which is why
  `--adopt` is the degraded option, not the default.

**Inspect history:**
```bash
$DARKARCHON_HOME/lib/tasks.sh list | today | failed | show <id> | result <id>
```

**Inspect / clean up teams:**
```bash
$DARKARCHON_HOME/lib/teams.sh list             # every team graded by last activity
$DARKARCHON_HOME/lib/teams.sh archive <team>   # move an unused team's state dir aside
```
- Archiving moves to `~/.darkarchon-archive/<date>/`, never deletes, and refuses
  any team whose tmux session is still alive.

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
