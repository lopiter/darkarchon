# darkarchon-orchestrators (Hermes plugin)

Turns [Hermes Agent](https://github.com/NousResearch/hermes-agent) into a
**fleet manager** for tmux-based Claude Code *orchestrator* sessions, built on
darkarchon's spawn/dispatch machinery.

```
Hermes (fleet manager)
 ├─ orchestrator-A  ← Claude Code in tmux window hermes:orchestrator-A
 │    └─ its own darkarchon worker team (DARKARCHON_TEAM=orchestrator-A)
 ├─ orchestrator-B
 └─ ...
```

Hermes ↔ orchestrator uses the same protocol darkarchon uses between an
orchestrator and its workers: full prompt written to a file, a short
`tmux send-keys` trigger, completion signalled by the orchestrator Writing a
result file (`dispatch-safe.sh` handles busy-checks, locking, nudge/timeout).

## What it registers

- **`orchestrator` tool** (toolset `orchestrator`) — actions:
  `set_team`, `spawn`, `dispatch`, `result`, `status`, `list`, `runs`,
  `interrupt`, `kill`.
  `dispatch` waits inline up to `wait_seconds` (default 120, max 540), then
  returns a `run_id` the model polls with `result`. Runs are recorded under
  `~/.darkarchon/<team>/hermes-runs/` and survive Hermes restarts.
- **`/orch` slash command** — fleet overview (`list`, `runs`) + `team [<name>]`.

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

| Variable            | Default            | Meaning                                          |
|---------------------|--------------------|--------------------------------------------------|
| `DARKARCHON_HOME`   | `~/work/darkarchon`| darkarchon checkout to shell out to              |
| `HERMES_ORCH_TEAM`  | `hermes`           | manager team → tmux session + `~/.darkarchon/<team>` |

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
