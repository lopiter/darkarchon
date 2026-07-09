# darkarchon

Coordinate multiple coding-agent CLIs — [Claude Code](https://claude.com/claude-code) and [OpenAI Codex](https://github.com/openai/codex) — across **separate tmux windows and repositories**. Each worker is an independent `claude` (or `codex`) process with its own cwd, skills, plugins, and MCP servers. File-based message passing. Live dashboard. tmux-native.

> tmux window = isolated claude process · filesystem = message bus · tmux send-keys = short trigger

Use when one claude session — or Claude Code's built-in `Task` sub-agent — can't cover the work. Sub-agents share the parent's cwd and `.claude/` config; darkarchon workers don't.

---

## Who this is for

If you've hit any of these walls, this tool was built for you.

### 1. You use Claude Code's `Task` tool, but your work spans multiple repositories

Sub-agents spawned by `Task` run inside the parent process — same cwd, same MCP config, same tool permissions. They can't open a sibling repo as if it were "their" workspace. darkarchon gives each worker its own tmux window started in its target repo, so a worker for `~/work/backend` has a fully independent `claude` session rooted there.

### 2. You can't use per-repo skills, plugins, or MCP servers across sub-agents

A `claude` session inherits the `.claude/` setup of its cwd — its skills, its plugins, its MCP server list. `Task` sub-agents reuse the parent's, so a skill installed for repo A is invisible when the sub-agent is asked to work in repo B. Each darkarchon worker is a **fresh `claude` process** started in its repo, which picks up that repo's `.claude/` natively.

### 3. You already have a Claude Code session running and don't want to throw it away

`invite-worker.sh <name> <session:window>` registers an existing pane as a worker without respawning it. The dashboard tracks it, dispatch routes to it, but the running conversation (and its `/compact` history) is preserved.

### 4. You live in tmux and find external UIs heavy

darkarchon is shell scripts + a small Python hub. Workers are `claude` processes in tmux windows you can `Ctrl-b w` to. The dashboard is **optional** — `lib/tasks.sh` and `questions.sh` give you the same data on stdout when you'd rather not leave the terminal.

### 5. You run multiple Claude Code instances and just want simple monitoring

`dashboard.sh start` → open `http://localhost:5173`. Every worker on every host you've started an agent on shows up, grouped by host → team, with live state (idle / busy / awaiting / rate_limited / compacting / dead). OS push when a worker needs you. No accounts, no cloud, no telemetry.

---

## Why this exists

Claude Code's built-in `Task` tool spawns sub-agents inside the same process. They share the parent's cwd, MCP config, and tool permissions. That's enough for most tasks, but breaks down when:

- you need to touch **multiple repositories** in parallel (each with its own `.claude/` config)
- a worker needs to run for hours independently while you work on something else
- you want to **see** what every worker is doing at once

darkarchon gives each worker its own tmux window — a full, independent claude process — and orchestrates them through small files on disk. A web dashboard streams every worker's state in real time.

---

## Install via Claude Code (recommended)

Open a Claude Code session in any directory and paste:

```
Install darkarchon from https://github.com/lopiter/darkarchon following its README quick start.

- Ask me where to clone it (default: ~/work/darkarchon). Clone there if it isn't
  already present, and use that path as DARKARCHON_HOME everywhere below.
- Run <clone-dir>/install.sh — it symlinks the darkarchon-team skill into
  ~/.claude/skills/ and appends DARKARCHON_HOME to my shell rc. If it doesn't fit
  my environment (fish, custom rc), reproduce those two effects by hand instead.
- Run `pip install --user mcp` to enable native MCP tools inside workers
- Verify with `python3 -c "import mcp; print('ok')"` and `ls -la ~/.claude/skills/darkarchon-team`
- Do NOT start the dashboard / agent / any worker yet — I'll do that manually
- Print a one-line summary of what changed at the end
```

Claude reads the rest of this README, decides which shell rc (`~/.zshrc` / `~/.bashrc`) you actually use, and adapts to your Python setup (system / pyenv / venv). One-shot install on most macOS / Linux setups.

`install.sh` is idempotent — re-run it any time; a real directory already at the
skill destination is backed up (`.bak.<timestamp>`), never deleted.

For a step-by-step manual install, see Quick start below.

### Drive it with natural language (skill)

You don't have to memorize the scripts. `skills/darkarchon-team/SKILL.md` is a
[Claude Code skill](https://docs.claude.com/en/docs/claude-code/skills) gated on
the phrase **"dark team"** — a bare "team" means Claude Code's native teams, so
the explicit phrase keeps the two from colliding. Say "create a dark team" and
Claude surveys your open tmux windows and asks which ones to enlist; say
"invite 10:1 to the dark team" to register a single pane. Once the context is
active, follow-ups like "have backend add a health endpoint" or "stop the team"
route to the right darkarchon commands. `install.sh` symlinks the skill into
`~/.claude/skills/darkarchon-team`, so `git pull` keeps it current — no
re-copying.

Want to tailor the team-naming / project conventions to your own setup? Replace
the symlink with a real copy under a different name (e.g.
`~/.claude/skills/my-team/`) and edit away; the bundled skill then serves as the
upstream reference.

---

## Quick start

```bash
# 1. clone (pick any directory you like; ~/work/darkarchon is just a convention)
git clone https://github.com/lopiter/darkarchon ~/work/darkarchon

# 2. shell rc
export DARKARCHON_HOME=~/work/darkarchon

# 3. (optional) install the python mcp SDK — enables native MCP tools
#    inside workers. Without this, workers fall back to invoking the
#    legacy ask.sh / mailbox.sh commands via Bash.
pip install --user mcp

# 4. spawn a worker (creates tmux window 'backend' in session $DARKARCHON_TEAM)
$DARKARCHON_HOME/lib/spawn-worker.sh backend ~/projects/backend python

# 5. dispatch a task and wait for the result
$DARKARCHON_HOME/dispatch-safe.sh backend 'add a /health endpoint that returns ok'

# 6. (optional) start the dashboard
$DARKARCHON_HOME/dashboard.sh start     # hub on http://localhost:8765 + hash(team) % 100
cd $DARKARCHON_HOME/dashboard-ui && npm install && npm run dev   # ui on http://localhost:5173
```

`spawn-worker.sh` creates the tmux session on demand if it doesn't exist. The worker runs `claude --permission-mode auto`, reads its role prompt from `prompts/`, and (when `mcp` is installed) loads the darkarchon MCP server as a child process.

> **First worker on a new folder?** Claude Code shows a one-time *"Is this a
> project you trust?"* prompt before it starts, so the worker looks stuck and
> the first dispatch will refuse it as not-yet-idle. Switch to the window
> (`Ctrl-b w`, pick the worker) and press **Enter** once to confirm. This is per
> repo/folder — subsequent spawns in the same folder skip it.

### Claude or Codex workers

Workers can be either Claude Code or OpenAI Codex. Pass `--kind` to `spawn-worker.sh`
(default `claude`); `invite-worker.sh` auto-detects the kind from the pane and
takes `--kind` only to override.

```bash
# spawn a Codex worker (needs the `codex` CLI installed + `codex login`)
$DARKARCHON_HOME/lib/spawn-worker.sh --kind codex reviewer ~/projects/backend review
# dispatch + dashboard are identical regardless of kind
$DARKARCHON_HOME/dispatch-safe.sh reviewer 'review the latest changes'
```

Codex workers launch as a persistent `codex --dangerously-bypass-approvals-and-sandbox`
TUI (no `codex exec`). The dispatch contract is the same one-line trigger; the
worker's kind is recorded in the registry so dispatch, busy-detection, and the
dashboard route to the right per-agent logic. Two notable differences from Claude:

- **No launch-time prompt / MCP injection.** Codex has no `--append-system-prompt`
  or `--mcp-config`; the team contract isn't written into the repo, and peer
  tools fall back to the legacy `ask.sh` / `mailbox.sh` path (MCP-less).
- **Same-cwd serialization.** A Claude dev worker and a Codex reviewer can share
  one repo — `dispatch-safe.sh` refuses a dispatch while a *same-cwd peer* is
  busy, so the two never edit the working tree concurrently.

---

## How it works

```
┌──────────────────────────────────────────────────────────────┐
│                        your machine                          │
│                                                              │
│  tmux session ($DARKARCHON_TEAM)                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐                    │
│  │ worker A │  │ worker B │  │ worker C │   (claude --auto)  │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘                    │
│       │             │             │                          │
│       │ writes tasks/, mailbox/, questions/ files            │
│       ▼             ▼             ▼                          │
│  ~/.darkarchon/$DARKARCHON_TEAM/{tasks, mailbox, ...}        │
│       ▲                                                      │
│       │ scans every 5s + tails tmux panes                    │
│  ┌────┴─────┐                ┌────────────┐                  │
│  │  agent   │ ── POST ──▶    │   hub      │ ── SSE ──▶ ui    │
│  └──────────┘                └────────────┘                  │
└──────────────────────────────────────────────────────────────┘
```

- **agent** (`agent.py`) — per-host process that scans tmux for claude panes, captures their state, merges in heartbeat freshness, and POSTs to the hub.
- **hub** (`dashboard.py`) — aggregator. Stores state by host, broadcasts SSE events, serves the React UI. Queries `tasks.db` for dispatch history.
- **dashboard-ui** — React + Vite app at `dashboard-ui/`. Subscribes to `/api/events` (SSE) and polls `/api/status` as a fallback.
- **dispatch** — writes a task row to `tasks.db`, sends a short tmux trigger, then tails the result file. Long payloads never go through tmux's send-keys (200-char limit, sentinel-parsing fragility). State transitions (pending → running → completed/failed/timeout) are validated by the SQLite task store.
- **worker-side MCP server** (`lib/mcp_server.py`) — child of the claude process. Exposes `ask`, `mailbox_send`, `mailbox_drain`, `status_get` as native tools. Same on-disk format as the legacy sh helpers — both paths interoperate.
- **heartbeat writer** (`lib/heartbeat-writer.sh`) — another child of the claude process. Touches `heartbeats/<worker>.json` every 5s while alive; the agent marks the worker dead the moment the file goes stale or the pid disappears.
- **state resolver** (`lib/worker_state.py`) — the single source of truth for "what state is worker X in?", shared by `dispatch-safe.sh`, `check-worker-state.sh`, and the dashboard agent. It layers three signals by reliability: heartbeat liveness (dead) > hook events > TUI scrape. No more drifting copies of busy/idle regex.
- **state hook** (`lib/state-hook.sh`) — for spawned Claude workers, `start-worker-claude.sh` injects a per-worker hooks file via `claude --settings` (same out-of-repo pattern as the MCP config). Claude Code fires `UserPromptSubmit`/`Stop`/`Notification`/`PreCompact` and the hook records busy/idle/awaiting_user/compacting to `states/<worker>.json` — event-driven, so the resolver isn't guessing from screen scraping. Invited/codex/legacy workers have no hook file and fall back to scraping, unchanged.

See [DESIGN.md](DESIGN.md) for the dashboard visual spec.

---

## Sequence flows

Renders inline on GitHub (Mermaid). For local viewing, any Markdown
preview tool that handles Mermaid works.

### 1. Worker spawn

```mermaid
sequenceDiagram
    actor User
    participant spawn as spawn-worker.sh
    participant lock as with_registry_lock
    participant fs as $STATE_DIR
    participant tmux as tmux
    participant wrapper as start-worker-claude.sh
    participant hb as heartbeat-writer.sh
    participant claude as Claude Code
    participant mcp as mcp_server.py

    User->>spawn: spawn-worker.sh backend ~/repo python
    spawn->>tmux: create session if missing
    spawn->>tmux: new-window 'backend' -c ~/repo
    spawn->>lock: registry write (locked)
    lock->>fs: workers-runtime.env += WORKER_backend_*
    lock->>fs: orchestrator.txt = pane id
    spawn->>tmux: send-keys "start-worker-claude.sh ..."
    tmux->>wrapper: exec in new pane
    wrapper->>fs: write mcp-config-backend.json
    wrapper->>hb: background spawn (every 5s while alive)
    hb-->>fs: heartbeats/backend.json (loop)
    wrapper->>claude: exec claude --mcp-config ... --permission-mode auto
    claude->>mcp: auto-spawn as stdio child (per --mcp-config)
    Note over claude,mcp: worker ready for dispatch
```

One spawn → 1 tmux window + 3 child processes (claude, heartbeat-writer, mcp_server). Same pid tree, die together.

### 2. Dispatch a task

```mermaid
sequenceDiagram
    actor User
    participant dispatch as dispatch-safe.sh
    participant disp as lib/dispatch.sh
    participant store as task_store.py
    participant fs as $STATE_DIR
    participant tmux as tmux
    participant claude as Claude Code worker

    User->>dispatch: dispatch-safe.sh backend 'add /health'
    dispatch->>tmux: check active marker (busy?)
    dispatch->>disp: delegate (idle)
    disp->>fs: /tmp/ee/p-<id>.txt = prompt
    disp->>store: insert row (pending)
    store->>fs: tasks.db append
    disp->>tmux: send-keys "Read p-<id> Write r-<id> output DONE-<id>"
    disp->>store: update-status → running

    loop poll for DONE marker (every 3s)
        disp->>tmux: capture pane content
    end

    claude->>fs: Read /tmp/ee/p-<id>.txt
    claude->>claude: do work
    claude->>fs: Write /tmp/ee/r-<id>.txt
    claude->>tmux: print "DONE-<id>"
    disp->>fs: read r-<id>.txt
    disp->>store: update-status → completed (+ result)
    disp->>User: stdout = result
```

Prompt / result travel via the filesystem (tmux's 200-char trigger budget is too tight). tmux only carries the short "go" signal. SQLite stores every state transition with state-machine validation.

### 3. Worker asks the user (MCP)

```mermaid
sequenceDiagram
    participant claude as Claude Code worker
    participant mcp as mcp_server.py
    participant fs as $STATE_DIR/questions/
    participant hub as dashboard.py (hub)
    participant ui as dashboard-ui
    actor User

    claude->>mcp: mcp__darkarchon__ask(question, context)
    mcp->>fs: questions/<id>.json (from_worker, body, pending)
    mcp-->>claude: "Question filed: <id>"

    Note over fs,hub: hub's background thread watches questions/
    fs->>hub: new file detected
    hub->>ui: SSE event 'new_question'
    ui-->>User: orange pulse on worker card + OS push

    User->>fs: questions.sh answer <id> "your answer"
    fs-->>claude: arrives in worker's mailbox.jsonl
    claude->>mcp: mcp__darkarchon__mailbox_drain()
    mcp->>fs: read + archive mailbox
    mcp-->>claude: drained messages
```

Worker never touches files directly. MCP tool → file → hub detects → SSE → UI. User's answer comes back via the mailbox file.

### 4. Dashboard data flow (multi-host)

```mermaid
sequenceDiagram
    participant agent_a as agent.py (host A)
    participant agent_b as agent.py (host B)
    participant tmux_a as tmux (host A)
    participant hb as heartbeats/*.json
    participant store as task_store.py
    participant hub as dashboard.py (hub)
    participant ui as dashboard-ui (browser)
    actor User

    loop every 5s
        agent_a->>tmux_a: scan_panes
        agent_a->>hb: read heartbeat files
        agent_a->>hub: POST /api/hosts/host-a/state (workers + heartbeat)
        agent_b->>hub: POST /api/hosts/host-b/state
    end

    Note over hub: in-memory store keyed by host

    User->>ui: open dashboard
    loop every 5s (poll)
        ui->>hub: GET /api/status
        hub->>store: list_recent_tasks per worker
        hub-->>ui: aggregated workers (host > team > worker)
    end

    Note over hub,ui: SSE channel for low-latency events
    hub-->>ui: state_change / new_question / mailbox_new
    ui-->>User: live UI
```

Each host runs its own `agent.py`. The hub aggregates by host id. SSE for instant pulses, polling for the periodic refresh.

### Common substrate

All four flows write/read the same on-disk layout:

```
              $STATE_DIR (single source of truth)
              ├── workers-runtime.env
              ├── tasks.db (SQLite)
              ├── questions/<id>.json
              ├── mailboxes/<worker>.jsonl
              ├── heartbeats/<worker>.json
              └── .registry.lock
                        ▲
        ┌───────────────┼────────────────┐
        │               │                │
   worker side     observer side    user side
  (spawn/dispatch/  (hub/agent/      (questions.sh /
   mcp/heartbeat)    dashboard-ui)    direct dispatch)
```

tmux carries short triggers only; the filesystem is the message bus.

---

## Commands

| Script | Purpose |
|---|---|
| `lib/spawn-worker.sh [--kind claude\|codex] <name> <cwd> [role]` | Create a new tmux window and start a Claude or Codex worker in it (default claude) |
| `invite-worker.sh [--kind claude\|codex] <name> <session:window> [role]` | Register an existing Claude/Codex pane as a worker (no respawn; kind auto-detected) |
| `uninvite-worker.sh <name>` | Remove an invited worker from the registry (pane untouched) |
| `dispatch-safe.sh <name> '<prompt>'` | Send a task, get the result. Refuses if the pane looks busy |
| `lib/dispatch.sh <name> '<prompt>'` | Same, without the busy-check |
| `lib/kill-worker.sh <name>` | Close the worker's tmux window and clean up the registry |
| `lib/start.sh` | Start every worker in `WORKERS=()` (config.env) |
| `lib/stop.sh` | Kill the team's tmux session |
| `lib/tasks.sh list \| today \| failed \| show <id> \| result <id>` | Inspect task history |
| `check-worker-state.sh <name>` | One-line state report — for orchestrators deciding whether to dispatch |
| `questions.sh list \| show <id> \| answer <id> "<text>"` | Read & answer worker-filed questions |
| `notify-watcher.sh [hub_url]` | Subscribe to hub SSE and emit macOS desktop notifications (terminal-notifier / osascript) |
| `dashboard.sh start \| stop` | Start/stop hub + agent daemons |
| `agent.sh start \| stop` | Start/stop just the per-host agent |
| `cleanup-serena.sh [--dry-run \| --all]` | Reap orphaned serena/lsp helper processes |

**Worker-side tools** (called from inside a worker — via MCP when available, sh as fallback):

| MCP tool | Legacy sh equivalent | Purpose |
|---|---|---|
| `mcp__darkarchon__ask(question, context)` | `lib/ask.sh "<q>"` | File a question for the orchestrator |
| `mcp__darkarchon__mailbox_send(to, body)` | `lib/mailbox.sh send <to> "<b>"` | Send a peer message + notify recipient |
| `mcp__darkarchon__mailbox_drain()` | `lib/mailbox.sh read <self>` | Read & remove own pending messages |
| `mcp__darkarchon__status_get()` | (no equivalent) | Self-introspection (mailbox count, recent tasks) |

Both write the same on-disk format under `$STATE_DIR/{questions,mailboxes}/`. The dashboard and `questions.sh` read either path identically — MCP is a more natural calling convention, not a different mechanism.

### Why two paths

Both reach the same files; what differs is how the worker calls them:

```
Legacy (sh, always available):                 MCP (when `pip install mcp` is present):

  worker claude                                  worker claude
       │  Bash tool                                   │  native tool call
       ▼                                              ▼
  /bin/sh fork                                   mcp_server.py (stdio child)
       │                                              │
       ▼                                              ▼
  ask.sh / mailbox.sh                            same write logic in Python
       │                                              │
       └────────────────────┬─────────────────────────┘
                            ▼
            $STATE_DIR/{questions,mailboxes}/...
                            │
                            ▼
                  hub + dashboard + questions.sh
                  (read identically either way)
```

Reasons to prefer MCP when available:

- **Tokens** — worker prompts don't need to teach the bash command shapes (~800 tokens saved per API call). Claude Code auto-injects the typed tool descriptions.
- **Latency** — stdio JSON-RPC ≈ 10× faster than fork + exec.
- **Reliability** — type-checked schema + structured errors vs. fragile stdout parsing.
- **Cleaner prompts** — the worker no longer has to remember quoting / escaping / exit code conventions.

Reasons the legacy sh path stays:

- **Zero new dependencies** — works without `mcp` package, useful for stripped-down environments.
- **Multi-LLM** — Codex workers (and other non-Claude CLIs) lack Claude Code's built-in MCP client, so the sh path is the only one available to them. Claude and Codex workers coexist in the same team because they share the on-disk file format.
- **Backward compat** — anything calling `ask.sh` / `mailbox.sh` directly (e.g. from `prompts/`-injected examples, ad-hoc shell sessions, or future tooling) keeps working unchanged.

---

## Configuration

Everything lives in `config.env` and is overridable by environment:

| Variable | Default | Purpose |
|---|---|---|
| `DARKARCHON_TEAM` | `default` | Team name. Becomes the tmux session name and parent of `~/.darkarchon/<team>/` |
| `DARKARCHON_PROMPT_DIR` | unset | Optional dir of project-specific prompts layered on top of generic `prompts/` |
| `CLAUDE_FLAGS` | `--permission-mode auto` | Passed to every spawned claude |
| `TASK_TIMEOUT` | `300` | Seconds before a dispatched task is considered stale |
| `POLL_INTERVAL` | `3` | Seconds between dispatch tail polls |

Per-shell isolation (recommended via [direnv](https://direnv.net/)):

```bash
# .envrc in your worktree / project dir
export DARKARCHON_TEAM=feature-x
```

Shells with different `DARKARCHON_TEAM` values land in different tmux sessions and write to different `STATE_DIR` — fully isolated. You can run several teams in parallel without collision.

---

## State directory layout

```
~/.darkarchon/<team>/
├── workers-runtime.env       # registry of spawned/invited workers
├── tasks.db                  # SQLite — every dispatch (status + result + history)
├── mailboxes/<worker>.jsonl  # inter-worker messages
├── questions/<id>.json       # worker→user clarifying questions
├── heartbeats/<worker>.json  # per-worker liveness (5s update, pid + last_seen)
├── mcp-config-<worker>.json  # generated per-spawn MCP server config
├── agent.config              # hub URL, polling interval
├── orchestrator.txt          # pane id of the session that spawned the team
└── .registry.lock            # transient mkdir-lock for concurrent mutations
```

`tasks.db` is the source-of-truth — query via `lib/tasks.sh` or any SQLite client. The plain-file siblings let you inspect / delete with `cat` / `ls` when needed.

---

## Dashboard

The React UI shows every worker grouped by host → team, with live state badges (idle, busy, awaiting_user, rate_limited, dead) and OS push notifications when a worker needs you. Dark mode only, desktop only. Dummy data mode (`VITE_USE_DUMMY=1`) available for visual development without a running hub.

---

## Multi-host setup

Run one hub and one agent per machine. The hub aggregates everyone's view; each agent reports its own host's tmux.

### Hub host (one machine — usually the one you sit in front of)

| Component | Why |
|---|---|
| `dashboard.sh start` | runs the hub (HTTP + SSE) AND the local agent |
| `dashboard-ui` (`npm run dev` or built bundle) | the browser-side UI |
| optional: `notify-watcher.sh` | macOS desktop notifications |

Defaults are fine on a single-LAN home network. The hub binds to `0.0.0.0` so other hosts can POST.

### Agent host (every other machine you want visible)

| Component | Why |
|---|---|
| `agent.sh start` | scans this machine's tmux + heartbeats, POSTs to the hub |

`~/.darkarchon/<team>/agent.config` (auto-created on first start) needs `HUB_URL` pointing at the hub host's LAN IP — e.g. `HUB_URL=http://192.168.x.x:8774`. The hub never reaches back; the agent always initiates.

### What stays host-local (does NOT cross hosts)

- `dispatch` (tmux-driven — sender's tmux can't reach the other host)
- `mailbox` / `questions` (filesystem-based — each host has its own `$STATE_DIR`)
- `tasks.db`, `heartbeats/`

The hub only mirrors **what each agent reports about its own host**. Cross-host worker collaboration is currently a non-goal — workers are expected to operate on their host; the dashboard merely gives one observation point.

### Updating an agent host after pulling new features

When the maintainer ships new agent capabilities (heartbeat, MCP, SQLite, etc.), each agent host needs its own pull:

```bash
# on the agent host
cd ~/work/darkarchon
git pull   # whichever remote you cloned from

# optional — enables MCP tools inside spawned workers on this host
pip install --user mcp

# restart the agent so it loads the new code
./agent.sh stop
./agent.sh start

# workers spawned BEFORE the pull won't have the new wrappers
# (no heartbeat-writer / no MCP server child / no state hooks). To pick them up:
./lib/kill-worker.sh <worker>
./lib/spawn-worker.sh <worker> <cwd> <role>
```

Until an agent host is on the new code, workers it owns will show `heartbeat_age_sec=null` on the dashboard — the dashboard side falls back to plain tmux-scan inference for those, so they're still visible, just less accurate.

**Re-spawn to gain event-driven state.** Hook-based state reporting (accurate `busy`/`idle`/`awaiting_user` without TUI scraping) is injected at launch via `claude --settings`, so a running Claude session cannot be upgraded in place — Claude Code snapshots its hook config at startup and won't adopt an externally-added one mid-session. `kill-worker` + `spawn-worker` (above) gives the worker its state hooks. Workers you'd rather not restart keep working on the scrape fallback; the dashboard just labels their state `source=scrape` instead of `source=hook`. Invited workers (`invite-worker.sh`) are always scrape-based by design — they exist precisely to preserve a running conversation.

**Scratch path moved.** Dispatch prompt/result scratch files moved from the world-readable `/tmp/ee` to a per-user `/tmp/darkarchon-<uid>` (mode 700) so other users on a shared host can't read a worker's prompts. Nothing to migrate — the files are transient (the durable copy lives in `tasks.db`); an in-flight dispatch started under the old path is read back from its recorded path and completes normally.

---

## Project status

The core loop is stable. What works today:

- spawn / dispatch / kill / mailbox / ask end-to-end (sh + MCP both paths)
- multi-host (run `agent.sh` on each machine, point at one hub)
- dashboard with OS push notifications, dead/stale detection via heartbeats
- SQLite task store with state-machine-validated transitions
- mkdir-based registry lock — concurrent spawn/kill no longer race
- worktree isolation by `DARKARCHON_TEAM` (no implicit path inference)
- unified state resolver (`lib/worker_state.py`): heartbeat > hook events > scrape, one copy shared by dispatch + dashboard
- event-driven state for spawned Claude workers via injected hooks (no TUI-glyph guessing); scrape fallback for invited/codex workers
- per-worker + per-cwd dispatch locks — a busy-check and its dispatch are atomic, so two dispatchers can't double-fire
- result-file-first completion + activity-based timeout (nudge once on a contract-less turn-end, hard cap `TASK_MAX_SECONDS`) — long tasks no longer die at a flat 300s

Rough edges:

- prompt layering knobs (`DARKARCHON_PROMPT_DIR`) are minimally documented
- no install one-liner / brew formula; clone-and-export only
- agent and hub run as foreground daemons via `dashboard.sh` (no launchd/systemd integration)
- tests are not yet CI-wired (they pass locally: `python3 -m pytest`)

Built for working with multiple claude instances on cross-repo refactors without switching tmux windows by hand.
