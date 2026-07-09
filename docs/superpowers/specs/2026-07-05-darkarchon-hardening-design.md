# darkarchon Hardening Design

> 2026-07-05. Unifies five weaknesses — state detection, dispatch races, the
> completion protocol, and IO vulnerabilities — into a single coherent design.
> This document is the single source of truth for the implementation.

## Background — the weaknesses we are fixing

The current darkarchon has a stable core loop, but its most frequently executed
decision logic stands on its most fragile signal source (scraping the Claude Code
TUI screen).

1. **State-detection copy divergence** — the busy/idle decision logic is split
   across three copies: `dispatch-safe.sh` (shell regex `CLAUDE_ACTIVE_PATTERN`),
   `check-worker-state.sh` (shell regex requiring the `✽` glyph — already an old
   pattern), and `detectors/claude.py` (tested Python, on the observer side). The
   two shell copies diverge from each other and from the Python detector. Observed
   real bugs: `check-worker-state.sh`'s `✽` requirement misjudges busy as idle when
   the glyph differs; the shell-side `still running` match misjudges "N shells still
   running" (actually idle) as busy.
2. **Dispatch TOCTOU** — there is no lock between the busy check and the actual
   dispatch, so if two orchestrators both pass the check simultaneously, one worker
   receives a double dispatch.
3. **Completion protocol depends on LLM compliance** — if a worker does not follow
   "Read p → Write r → print DONE", failure only surfaces after hitting the
   `TASK_TIMEOUT=300s` wall. The README's "multi-hour independent work" use case
   contradicts the 300-second default.
4. **`/tmp/ee` vulnerability** — prompt exposure on multi-user machines (permissions
   unrestricted), and loss on reboot.

## Fixed constraints (from the brainstorming Q&A)

- **Worker execution model: keep the TUI.** Every worker is an interactive tmux
  session. attach/converse/invite are the core value. No headless dispatch mode
  will be introduced.
- **invite workers: scraping fallback.** Hooks cannot be injected into an
  already-running session (`--settings` is a launch flag, and Claude Code snapshots
  and freezes the hook configuration at start time — external injection while
  running is impossible). Therefore invite/codex/older workers continue to operate
  via the existing scraping path.
- **State-decision unification: a Python core resolver.** Remove the shell regex
  copies and consolidate around the tested Python detector.

## §1. State-resolver unification — `lib/worker_state.py`

The single module that answers "what is worker X's current state?"

### Decision order (layers)

1. **Liveness** — tmux session/window absent, or the heartbeat pid dead/stale →
   `dead`. (reuses `lib/heartbeat.py`)
2. **Hook event file** — if `$STATE_DIR/states/<safe_name>.json` exists and the
   heartbeat is alive (i.e. the hook is fresh), it is the **authoritative source**
   for busy/idle/awaiting_user/compacting.
3. **Scraping fallback** — if there is no hook file, tmux capture + the existing
   detector (`detectors/claude.py` · `codex.py`, reused as-is as pure functions).
   The resolver performs the capture.

### hook ↔ scrape synthesis rule (stateless, fresh on every call)

```
resolve(name) →
  1. session dead or heartbeat dead        → {state: dead}
  2. scrape = capture + detector(kind)      # always computed (for de-escalation)
  3. hook = read_hook_state(name); fresh = hook exists and not dead
  4. if fresh:
       - hook.state == busy and scrape.state in (idle, unsent):
             → scrape           # self-heal for stuck-busy where hook missed the Stop event
                                #   source = "scrape(hook-stale)"
       - hook.state == idle and scrape.state == unsent:
             → unsent           # user is typing into the prompt (hook can't see it)
                                #   source = "scrape-overlay"
       - else: → hook           # source = "hook"
  5. else: → scrape             # source = "scrape"
```

Key rationale:
- **typed-unsent (residual input) has no hook event** → even for a hook worker, this
  one item is synthesized from scraping. unsent only when hook.idle + scrape.unsent.
- **stuck-busy self-heal** — if the hook misses a Stop once and freezes on busy, the
  moment scrape sees an empty prompt (idle) it trusts scrape, preventing an infinite
  block. The Claude spinner is reliably captured as gerund+… during real work, so the
  risk of a false idle from scrape is low.

### Normalized state vocabulary

`dead, rate_limited, error, awaiting_user, compacting, busy, unsent, idle, unknown`

detector output mapping: claude `typed`→`unsent`; codex `error`→`error`. The hook
event mapping is in §2.

### CLI / consumers

- `python3 lib/worker_state.py <name>` → a single `KEY=VALUE` line (existing
  check-worker-state format + a `source=hook|scrape|...` field), a `--json` option,
  and `--verbose` for a pane tail.
- The registry (target/kind/cwd) is read directly by the resolver via
  `parse_registry_file` → the shell wrapper only needs to pass STATE_DIR/name.
- `check-worker-state.sh` → a thin wrapper that calls the resolver.
- `dispatch-safe.sh` Checks 1–3 → replaced by a single resolver call (shell regex
  deleted).
- observer (`agent.py`) → imports the hook overlay function (§6).

## §2. Hook-based state events (spawn Claude workers only)

### Injection

`start-worker-claude.sh` generates `$STATE_DIR/hooks-settings-<worker>.json` using
the same pattern as mcp-config and passes it via `claude --settings <file>`. Because
the file lives **outside the worker repo** (in `$STATE_DIR`), it auto-merges with the
repo's own `.claude/` settings and leaves nothing behind. (This adds one flag to an
injection method already validated with `--mcp-config`.)

### Event → state mapping

| Hook event         | State           | Notes |
|--------------------|-----------------|------|
| `UserPromptSubmit` | busy            | turn start |
| `Stop`             | idle            | turn end |
| `Notification`     | awaiting_user   | permission requests etc. — carry the message as detail (better dashboard display quality) |
| `PreCompact`       | compacting      | |
| `SessionEnd`       | ended → dead    | |

The last event wins (last-event-wins). Because liveness is a higher layer, a stuck
event is overridden by heartbeat death, and the case of a live worker that missed an
event is healed by the §1 scrape de-escalation.

### Receiver — `lib/state-hook.sh <worker> <state>`

- Extracts detail (e.g. the Notification message) from the hook JSON on stdin.
- Writes a tmp file and updates `$STATE_DIR/states/<safe>.json` atomically via `mv`.
- **Always exit 0** — a hook failure must not block the worker. If settings validation
  fails, Claude Code ignores the file, so the worker is unaffected (it naturally
  de-escalates to scraping).

### Trust rule

The hook file is trusted only while the heartbeat is alive (§1 layer 1 comes first).
A worker with no heartbeat (invite/codex/legacy) also has no hook file, so it
naturally falls back to scraping.

## §3. Dispatch lock (eliminating TOCTOU)

- `$STATE_DIR/locks/dispatch-<safe>.lock/` — the same atomic mkdir pattern as the
  existing `.registry.lock`, recording the owner pid inside the lock directory.
- Acquired **before** the busy check, held until the task completes/fails (released
  via `trap EXIT`).
- **stale steal** — if the lock owner pid is dead, steal it and proceed.
- same-cwd serialization also uses a per-cwd lock (`locks/cwd-<hash>.lock`) to close
  the check-then-dispatch gap (promoting the current Check 4 to a lock).
- Add `with_dispatch_lock` / `with_cwd_lock` helpers to `_lib.sh` (same style as the
  existing `with_registry_lock`).
- `dispatch-safe.sh` changes from `exec dispatch.sh` to a child call so it can
  delegate while still holding the lock.

## §4. Completion protocol + timeout redesign

- **Completion signal = result file first** — bring `[ -s r-<id>.txt ]`, which codex
  already writes, to the claude path as the primary signal. Demote the 2× DONE-marker
  count to a secondary confirmation/logging role (removing scrollback/wrapping
  vulnerability).
- **Activity-based timeout** — keep waiting while the resolver reports busy. Two
  termination conditions:
  - **hard cap** `TASK_MAX_SECONDS` (default 3600) exceeded → timeout.
  - **turn-end without result** — the worker returned to idle but there is no result
    file: nudge once (re-trigger it to Write the result to r-<id>.txt) and, if still
    absent, fail early. To avoid false idle, a scraping-fallback worker is only judged
    as turn-end after **N consecutive (default 3) idle observations**.
- Effect: long-running work is not cut off + a worker that ignores the protocol fails
  clearly within ~1 minute rather than 300 seconds. This resolves the `TASK_TIMEOUT=300`
  contradiction (→ `TASK_MAX_SECONDS`, keeping the existing `TASK_TIMEOUT` as a
  backward-compatible alias).

## §5. IO directory hardening

- `/tmp/ee` → `/tmp/${TOOL_PREFIX}-$UID/` (`mkdir -m 700`, file umask 600). Blocks
  multi-user exposure. At ~35 characters the path stays within the 200-character
  trigger budget (the existing length guard is retained).
- Loss on reboot is acceptable — the prompt/result originals are already stored in
  `tasks.db`, so this is not data loss. An in-flight task reads its path from the
  tasks.db row, so an old-path task is also handled naturally.
- Add an `io_dir()` helper (path computation + mkdir) to `_lib.sh`, used by dispatch.sh.

## §6. Observer overlay / testing / migration

- **Observer hook overlay** — `agent.py` overlays hook state onto the tmux scan
  result so the dashboard also gets awaiting_user's rich detail (the permission
  message). `annotate_workers_with_hooks` (worker_state.py) follows the same pattern
  as the heartbeat `annotate_workers`. With no hook, the scan result is used as-is
  (no effect).
- **Testing** — `tests/test_worker_state.py`: hook>scrape priority, dead priority,
  stuck-busy de-escalation, idle+unsent overlay, staleness, `state-hook.sh`
  atomicity, lock steal. The existing detector tests remain valid.
- **Migration** — the README documents re-spawning workers on upgrade (expanding the
  existing "Updating an agent host" section) and mentions the `/tmp/ee` path change.
  Hooks apply from re-spawned workers onward; existing workers keep operating via
  scraping (no downtime).

## What we are intentionally NOT doing

- headless dispatch mode (excluded by the Q&A)
- invite-worker settings injection / repo file modification (structurally impossible +
  excluded by the Q&A)
- README strategy re-positioning (unrelated to code, a separate task)
- a full repair of the broken existing pytest fixtures (out of scope — only clean up
  conftest as far as the new tests need to run)

## File-change summary

New: `lib/worker_state.py`, `lib/state-hook.sh`, `tests/test_worker_state.py`, this
design document.
Modified: `lib/start-worker-claude.sh` (§2), `check-worker-state.sh` (§1),
`dispatch-safe.sh` (§1·§3), `lib/dispatch.sh` (§4·§5), `lib/_lib.sh` (lock·io_dir),
`agent.py` (§6 overlay), `config.env` (TASK_MAX_SECONDS), `README.md` (migration).
