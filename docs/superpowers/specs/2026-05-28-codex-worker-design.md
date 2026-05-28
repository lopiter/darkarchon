# Codex Worker Integration — Design Spec

**Date:** 2026-05-28
**Status:** Approved (design), pending implementation plan
**Author:** orchestrator + omc (codex facts verified empirically by omc on codex-cli 0.34.0)

## Goal

Let darkarchon bring **OpenAI Codex** into a team as a tmux-pane worker, alongside
the existing Claude Code workers. A codex worker can be spawned (or an existing
codex pane invited), dispatched tasks, and have its busy/idle state detected — at
parity with Claude workers for the **task-executor** use case.

## Background: how Claude workers work today

- **Launch** (`lib/spawn-worker.sh` → `lib/start-worker-claude.sh`): new tmux window
  runs `claude --append-system-prompt "<team contract>" --permission-mode auto
  --mcp-config <file>`. A heartbeat writer tracks the pid.
- **Dispatch** (`lib/dispatch.sh`): writes the full prompt to `/tmp/ee/p-<id>.txt`,
  sends one line via `tmux send-keys` —
  `Read /tmp/ee/p-<id>.txt then Write final answer to /tmp/ee/r-<id>.txt then output DONE-<id>` —
  then polls the pane for the literal `DONE-<id>` marker (≥2 occurrences) and reads
  the result file.
- **Busy/idle** (`dispatch-safe.sh` + `lib/detectors/claude.py`): scrapes pane text;
  Claude busy = spinner glyph + `…ing…`/`중 …`; prompt char `❯`.
- **Registry** (`$STATE_DIR/workers-runtime.env` via `lib/_lib.sh`): per worker
  `WORKER_<sn>_{NAME,TARGET,DIR,ROLE,EXTERNAL}`, keyed by `safe_name`.
- **Task store** (`lib/task_store.py`): SQLite, WAL + `busy_timeout=10000`. Tasks keyed
  by unique `TASK_ID = <date>-<time>-<rand hex>`.

## Verified codex facts (from omc, codex-cli 0.34.0)

- **Persistent interactive pane**, NOT `codex exec`. Launch with
  `codex --dangerously-bypass-approvals-and-sandbox`, **no positional prompt**
  (positional starts a session immediately).
- **No `--append-system-prompt`** equivalent. Contract injection options: cwd
  `AGENTS.md` (auto-read) or per-dispatch prompt. (We choose neither — see decisions.)
- **No `--mcp-config`** flag. MCP servers configured via `~/.codex/config.toml`.
- **Dispatch contract works**: codex has Read/Write/shell tools and obeys the
  one-line trigger. Send with `tmux send-keys -t <pane> -l -- "<single line>"` then
  `Enter`. **Single line only** (embedded `\n` submits early). copy-mode guard
  recommended (`#{pane_in_mode}`).
- **Completion**: result-file polling is more robust than stdout marker counting
  (codex alt-screen + wrapping makes marker counts less reliable).
- **Busy/idle strings** (real captures):
  - busy line: `Working (<N>s • Esc to interrupt)` → match `Working \(\d+s` or `Esc to interrupt`.
  - idle footer (always visible, even while busy): `⏎ send   ⌃J newline   ⌃T transcript   ⌃C quit`.
  - composer prompt is `▌` (U+258C), **not** `❯`. Do NOT use `❯`/footer-alone for idle.
  - capture with `tmux capture-pane -p` (no `-S`; codex uses alternate screen).
- **Auth trap**: codex needs `~/.codex/auth.json` (ChatGPT login) or `OPENAI_API_KEY`.
  Expired token surfaces `Failed to refresh token: 401 Unauthorized` / `stream error`.
  (This dev machine is currently 401-expired — live e2e needs `codex login` first.)
- **PATH**: tmux panes are non-login shells; wrap launch in a login shell
  (`zsh -lc 'exec codex …'`) so brew/nvm paths resolve.
- pane death (process) vs busy/idle (app): `#{pane_dead}` == 1 for process death.

omc reference files (oh-my-claudecode): `src/team/model-contract.ts`,
`src/team/tmux-session.ts`, `src/team/runtime.ts`, `scripts/run-provider-advisor.js`.

## Decisions

1. **Scope (v1):** spawn + dispatch + busy-detection + invite + dashboard for codex,
   plus same-repo collision handling. Out of scope: codex peer-messaging/ask,
   `AGENTS.md` injection, worktree isolation, gemini, driver abstraction.
2. **Same-cwd collision:** cwd guard + serialization. Warn on spawn/invite when cwd
   collides; dispatch-safe refuses when a same-cwd peer worker is busy.
3. **Codex capability:** task-executor only. No peer-messaging/ask in v1. No contract
   file written into the repo (avoids polluting an existing `AGENTS.md`). The
   one-line dispatch trigger is self-contained. Launcher still exports `EE_*` env for
   future legacy-fallback peer support.
4. **Approach:** A — KIND-parameterize the existing scripts (no parallel script family,
   no driver abstraction). Claude path is unchanged when KIND is absent/`claude`.

## Design

### §1 Registry — worker KIND
- New field `WORKER_<sn>_KIND` ∈ {`claude`, `codex`}; absent ⇒ `claude` (back-compat).
- `lib/_lib.sh`: add `worker_kind()` (mirrors `worker_target/dir/role`, defaults `claude`).
- Written by `spawn-worker.sh` and `invite-worker.sh`.

### §2 spawn-worker.sh — `--kind` flag + launcher routing
- New usage: `spawn-worker.sh [--kind claude|codex] <name> <cwd> [<role>]`. Default
  `claude` (existing positional calls unaffected).
- codex ⇒ invoke `lib/start-worker-codex.sh`; persist `WORKER_<sn>_KIND`.

### §3 start-worker-codex.sh (new launcher)
- Login-shell wrap: `zsh -lc 'exec codex $CODEX_FLAGS [--model $CODEX_MODEL]'`.
- `CODEX_FLAGS` default `--dangerously-bypass-approvals-and-sandbox`; `CODEX_MODEL` optional.
- **No** positional prompt, `--append-system-prompt`, or `--mcp-config`.
- Export `EE_WORKER_NAME/EE_TEAM_ROOT/EE_STATE_DIR/EE_ROLE`; start heartbeat writer (same as claude).
- Pre-flight: if `~/.codex/auth.json` missing AND `OPENAI_API_KEY` unset, print a warning.

### §4 dispatch.sh — kind-aware send + completion
- Resolve `KIND=$(worker_kind "$WORKER")`.
- **send-keys:**
  - claude (unchanged): `send-keys "$TRIGGER"` → 0.6s → `Enter`.
  - codex: `send-keys -l -- "$TRIGGER"` → 0.15s → `Enter`; one verify-retry (re-send Enter if the typed line hasn't cleared).
- **completion detection:**
  - claude (unchanged): poll pane for `DONE-<id>` ≥2, then read result file.
  - codex: poll for **result file non-empty** as the primary signal; `DONE-<id>` is
    best-effort secondary. Use `capture-pane -p` without `-S`.
- Timeout/`TASK_TIMEOUT` unchanged.

### §5 Busy/idle detection
- **lib/detectors/codex.py** (new) `classify_codex_state(plain, with_ansi)`:
  1. auth/stream error (`Failed to refresh token|401 Unauthorized|stream error`) ⇒ `{state:"error"}`.
  2. busy (`Working \(\d+s` or `Esc to interrupt`) ⇒ `{state:"busy"}`.
  3. idle (footer ASCII `send`+`newline`+`transcript` present AND no busy line) ⇒ `{state:"idle"}`.
  4. else ⇒ `idle` (conservative) — but dispatch-safe primarily keys off busy.
  Match ASCII substrings to avoid unicode-glyph regex breakage.
- **dispatch-safe.sh:** read kind; pick the active pattern.
  - claude: existing spinner + `❯` typed-input check.
  - codex: busy pattern only; auth-error ⇒ refuse with "codex not logged in / token
    expired" (new exit code **12**). Skip the `❯` typed-input check (codex composer is `▌`).
- **dashboard.py:** route to `classify_codex_state` vs `classify_claude_state` by kind.

### §6 cwd collision guard + serialization
- `lib/_lib.sh`: `workers_sharing_dir <dir> [<exclude_name>]` → other workers with same `DIR`.
- spawn/invite: cwd collides with an existing worker's DIR ⇒ **warning** (not fatal),
  naming the conflicting worker(s) + noting concurrent dispatch is serialized.
- dispatch-safe: after the target idle check, if any **same-cwd peer is busy** (using
  that peer's kind-appropriate detector), refuse (new exit code **13**) → serializes
  edits across claude+codex on one repo.

### §7 invite-worker.sh — codex detection + `--kind`
- Add `--kind` override.
- Auto-detect: pane shows codex markers (`Esc to interrupt`, footer
  `send`/`newline`/`transcript`, `Codex`) ⇒ codex; else existing claude heuristic.
- Persist `KIND` + `EXTERNAL=1`.

### §8 config.env knobs
- `CODEX_FLAGS="--dangerously-bypass-approvals-and-sandbox"`, optional `CODEX_MODEL=""`.

## What is NOT at risk (same-repo claude+codex coexistence analysis)

- **Dispatch IO files** (`/tmp/ee/p-,r-`): per-task unique IDs ⇒ no cross-talk.
- **Task DB**: WAL + busy_timeout ⇒ concurrent dispatch-safe; `worker` column separates.
- **Registry**: keyed by `safe_name`; distinct names enforced (spawn/invite reject dups).
- The genuine shared resource is the **git working tree**, addressed by §6.
- The genuine **correctness** risk is detector cross-talk (claude regex on a codex
  pane ⇒ false-idle ⇒ dispatch into a busy codex), addressed by §1+§5 (kind-aware).

## New exit codes (dispatch-safe.sh)
- `12` — codex auth/stream error (refuse).
- `13` — same-cwd peer worker busy (refuse, serialization).

## Testing
- **Unit:** `lib/detectors/codex.py` against real captures — busy
  `Working (0s • Esc to interrupt)`, idle footer
  `⏎ send   ⌃J newline   ⌃T transcript   ⌃C quit`, auth
  `Failed to refresh token: 401 Unauthorized`. Mirror existing detector tests.
- **Manual e2e:** spawn a codex worker → trivial dispatch → verify result file +
  busy detection + cwd-collision refusal. **Requires `codex login` first** (machine
  token is 401-expired).
