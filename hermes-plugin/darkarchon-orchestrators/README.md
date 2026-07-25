# darkarchon-orchestrators (Hermes plugin)

Turns [Hermes Agent](https://github.com/NousResearch/hermes-agent) into a
**fleet manager** for tmux-based Claude Code *orchestrator* sessions, built on
darkarchon's spawn/dispatch machinery.

```
Hermes (fleet manager)
 ├─ tmux session "orchestrator-A"
 │    ├─ window orchestrator-A   ← the employee (Claude Code, role=orchestrator)
 │    └─ windows of its own darkarchon workers (DARKARCHON_TEAM=orchestrator-A)
 ├─ tmux session "orchestrator-B"
 └─ ...
```

Each employee owns a **dedicated tmux session named after it** (spawned via
`spawn-worker.sh --session`); its sub-workers join that same session. The
fleet's registry/state stay together under `~/.darkarchon/<fleet-team>/`.
`kill` tears down the whole employee session (employee + workers) and its
sub-team registry, keeping task history.

Hermes ↔ orchestrator uses the same protocol darkarchon uses between an
orchestrator and its workers: full prompt written to a file, a short
`tmux send-keys` trigger, completion signalled by the orchestrator Writing a
result file (`dispatch-safe.sh` handles busy-checks, locking, nudge/timeout).

## What it registers

- **`orchestrator` tool** (toolset `orchestrator`) — actions:
  `set_team`, `spawn`, `invite`, `uninvite`, `dispatch`, `result`, `status`,
  `list`, `runs`, `questions`, `answer`, `interrupt`, `kill`.
  `invite` adopts an already-running Claude session (`session:window`) as an
  EXTERNAL employee: dispatchable, but state is scrape-detected (no hooks),
  no orchestrator contract prompt, and it can never be killed by the manager
  — only `uninvite`d (registry removal; the session is left untouched).
  `dispatch` waits inline up to `wait_seconds` (default 120, max 540); if the
  run outlives that window, a watcher thread takes over and INJECTS a
  completion report into the conversation when it finishes
  (`ctx.inject_message`) — Hermes reports the outcome on its own, no polling.
  Runs are recorded under `~/.darkarchon/<team>/hermes-runs/` and survive
  Hermes restarts (notifications require Hermes to be running; catch up on
  missed ones via `runs`/`result`). Fleet
  dispatches run with `TASK_MAX_SECONDS=7200` (unless the env overrides it)
  so the manager's clock outlives the orchestrator's own 3600s worker cap.
  `questions`/`answer` surface questions orchestrators escalate via `ask`
  (their only upward channel) and deliver the user's decision back by
  mailbox.
- **`/orch` slash command** — fleet overview (`list`, `runs`, `questions`) +
  `team [<name>]`.
- **`/to <employee> <task>`** — deterministic quick dispatch: no LLM
  interpretation, immediate state pre-check (busy/dead/awaiting refused with
  a clear message), fire-and-notify (completion arrives via the watcher).
  In plain chat, starting a message with `@<employee>` steers the agent to
  dispatch the rest to that employee. Both accept a **unique name prefix**
  (`/to inf …` reaches `influencer-specialist`); ambiguous prefixes are
  refused with the candidate list — `dispatch`/`status` tool actions resolve
  prefixes the same way. `kill`/`invite`/`uninvite` require exact names.

## Employee model

- **The fleet session name is not preset.** On first use the agent asks the
  user what to call it (`set_team`, persisted in
  `~/.hermes/darkarchon-orchestrators.json`; `HERMES_ORCH_TEAM` env overrides).
- **Each orchestrator is a long-lived employee.** `spawn` takes an optional
  `brief` — a job charter (who they are, what they own, standards) written to
  `~/.darkarchon/<team>/context/<name>/orchestrator.md` and layered into the
  session's system prompt at launch. The tool prompt steers the model to reuse
  idle employees whose charter fits instead of hiring duplicates, and to kill
  only on user request.

## Install

Easiest: run the installer (idempotent; also enables the plugin):

```bash
$DARKARCHON_HOME/hermes-plugin/install.sh          # into ${HERMES_HOME:-~/.hermes}
```

**Letting Hermes install itself**: paste the contents of
[`../INSTALL-VIA-HERMES.md`](../INSTALL-VIA-HERMES.md) into a Hermes chat —
the agent runs the whole procedure (clone → install.sh → config check →
verify) on its own instance.

Manual alternative:

```bash
ln -sfn "$DARKARCHON_HOME/hermes-plugin/darkarchon-orchestrators" \
        ~/.hermes/plugins/darkarchon-orchestrators
```

Then in `~/.hermes/config.yaml`:

```yaml
plugins:
  enabled:
    - darkarchon-orchestrators
platform_toolsets:
  cli:            # only if you maintain an explicit toolset list
    - orchestrator
```

Restart Hermes to load it.

## Configuration (env)

| Variable                    | Default            | Meaning                                          |
|-----------------------------|--------------------|--------------------------------------------------|
| `DARKARCHON_HOME`           | `~/work/darkarchon`| darkarchon checkout to shell out to              |
| `HERMES_ORCH_TEAM`          | (asked at first use)| fleet name override → `~/.darkarchon/<team>`    |
| `HERMES_ORCH_SLACK_WEBHOOK` | (unset = disabled) | Slack incoming-webhook URL; mirrors completion/failure reports and new employee questions to Slack. Set it in `~/.hermes/.env` so the background gateway sees it too (see below). |

## Slack gateway (two-way, optional)

To command the fleet FROM Slack, set up a hermes Slack gateway (Socket Mode
app via `hermes slack manifest` + `hermes gateway setup`; remember
`SLACK_ALLOWED_USERS=<your member id>` — an empty allowlist denies everyone).
Expose ONLY the orchestrator toolset to the Slack surface:

```yaml
platform_toolsets:
  slack:
    - orchestrator
```

Small routing models reliably pick the right tool when it is the only one;
mixing in file/terminal makes them wander (and a phone surface should not
have terminal access anyway). Completion reports for gateway-dispatched runs
arrive via the webhook channel (in-conversation injection is CLI-only).

## Slack notifications

Create an incoming webhook (api.slack.com/apps → Incoming Webhooks → pick a
channel), then put it in **`~/.hermes/.env`** (NOT your shell profile):

```
HERMES_ORCH_SLACK_WEBHOOK=https://hooks.slack.com/services/...
```

`~/.hermes/.env` is loaded into the environment of *every* Hermes process,
including the launchd/systemd gateway — a webhook exported only in `~/.zshrc`
is invisible to the background gateway, so whichever process claims the
notification marker first (often the gateway) would silently drop the send.
Restart Hermes (and `hermes gateway restart`) after setting it. You then
get `:white_check_mark:/:x:` messages when dispatch runs finish and
`:question:` messages the moment an employee escalates a decision — the
question watcher polls the fleet queue every 15s and notifies each question
exactly once, even when several hermes processes (CLI session + messaging
gateway) share one HERMES_HOME: the right to notify is claimed via an
atomic filesystem marker under `<state>/notified-questions/`. Slack
delivery is best-effort and never blocks fleet operation; messages are
prefixed with the fleet name so machines sharing a channel stay
distinguishable.

**Direct-work notifications**: when you attach to a spawned employee's pane
and type there yourself (no dispatch), its state hooks still record every
turn — the watcher turns those into Slack pings too: `:zzz:` when a turn
lasting ≥ `HERMES_ORCH_DIRECT_NOTIFY_MIN_SECONDS` (default 60) ends, and
`:keyboard:` the moment the pane hits a permission prompt / awaiting-input
state (dispatched or not). Turns belonging to a dispatch run are suppressed
(the run watcher already reports those). Disable with
`HERMES_ORCH_DIRECT_NOTIFY=0`. Invited (hook-less) employees are not
covered — only spawned ones.

## How the namespacing works

- Fleet-level calls run with `DARKARCHON_TEAM=$HERMES_ORCH_TEAM`, so
  orchestrator windows live in the tmux session named after the manager team
  and the registry/state in `~/.darkarchon/<manager-team>/`.
- Each orchestrator is spawned with `spawn-worker.sh --env
  DARKARCHON_TEAM=<name> <name> <cwd> orchestrator`, so darkarchon commands *it*
  runs operate on its own private team — no collisions between orchestrators or
  with the manager registry.
- The `orchestrator` role contract is injected at spawn from
  `prompts/orchestrator.md` (layered on the generic `prompts/all.md` worker
  contract by `start-worker-claude.sh`).
