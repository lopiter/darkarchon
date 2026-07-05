# Claude-driven install for first-time cloners

**Date:** 2026-07-05
**Status:** approved

## Problem

A fresh `git clone` of darkarchon leaves the user with four manual steps
scattered across the README: copy the bundled skill into `~/.claude/skills/`
(a copy that silently goes stale on every `git pull`), add `DARKARCHON_HOME`
to the shell rc, optionally `pip install mcp`, and figure out which of these
are actually required. The existing "Install via Claude Code" prompt covers
clone + rc + mcp but **not** the global skill install.

## Distribution story

Humans paste one prompt block into any Claude Code session; their Claude
performs the install. The repo's job is to provide material Claude can follow
deterministically:

- an **example `install.sh`** — idempotent, human-readable, runnable as-is on
  standard setups; Claude falls back to reproducing its effect by hand when
  the environment differs (fish shell, unusual rc, etc.)
- a **README prompt** that instructs Claude to run it (or match its effect)

`install.sh` is an example first, an installer second. It is not expected to
handle every environment; Claude is the compatibility layer.

## Decisions

1. **Skill install = symlink by default** (dotfiles pattern):
   `~/.claude/skills/darkarchon-team` → `<repo>/skills/darkarchon-team`.
   `git pull` then updates the skill for free. Users who want to customize
   copy instead of linking and rename the directory — the old "copy-me"
   philosophy is demoted to an escape hatch, documented in one line.
2. **install.sh scope = skill symlink + rc guidance only.** It creates the
   symlink (backing up any real file/dir it displaces, timestamped) and
   appends `export DARKARCHON_HOME=<repo>` to the detected rc if missing.
   `pip install mcp` and dashboard npm install stay in the README/prompt —
   they are environment-sensitive and optional.
3. **SKILL.md gets a `DARKARCHON_HOME` fallback note** — if the var is unset
   (fresh shell right after install), resolve the skill symlink or default to
   `~/work/darkarchon` before failing.

## Components

- `install.sh` (repo root) — idempotent; safe to re-run. Symlinks the skill,
  patches the rc (zsh/bash detection via `$SHELL`), prints what it did.
  No mcp/npm/tmux work.
- `README.md` — "Install via Claude Code" prompt gains the skill-symlink step
  and points at `install.sh`; the "Drive it with natural language" section is
  rewritten around symlink-default + copy-to-customize.
- `skills/darkarchon-team/SKILL.md` — fallback path resolution note.

## Error handling

- Existing real file/dir at the skill destination → moved to
  `<dst>.bak.<timestamp>`, never deleted.
- Correct symlink already present → no-op, prints `OK`.
- rc already contains `DARKARCHON_HOME` → no duplicate append.
- Unrecognized shell → print the export line and ask the user (or the
  installing Claude) to add it manually; exit 0 with a warning, since the
  symlink half still succeeded.

## Testing

Manual: run `install.sh` twice from a scratch clone; verify idempotency,
backup behavior with a pre-existing copied skill dir, and rc non-duplication.
No pytest coverage — the repo's test suite targets the Python hub/agent, and
the script's contract is exercised by every Claude-driven install.
