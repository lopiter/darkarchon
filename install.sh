#!/usr/bin/env bash
# darkarchon install — example script for the Claude-driven install flow.
#
# What it does (idempotent; safe to re-run):
#   1. Symlink ~/.claude/skills/darkarchon-team -> <this repo>/skills/darkarchon-team
#      so `git pull` updates the skill in place. A real file/dir already at the
#      destination is backed up, never deleted.
#   2. Append `export DARKARCHON_HOME=<this repo>` to your shell rc if missing.
#
# What it deliberately does NOT do: `pip install mcp`, dashboard npm install,
# tmux checks — those are environment-sensitive and optional; see README.
# If this script doesn't fit your setup (fish, custom rc), reproduce the two
# effects above by hand — that's the whole contract.

set -euo pipefail

DARKARCHON="$(cd "$(dirname "$0")" && pwd)"

# --- 1. global skill symlink -------------------------------------------------

SKILL_SRC="$DARKARCHON/skills/darkarchon-team"
SKILL_DST="$HOME/.claude/skills/darkarchon-team"

mkdir -p "$(dirname "$SKILL_DST")"

if [ -L "$SKILL_DST" ] && [ "$(readlink "$SKILL_DST")" = "$SKILL_SRC" ]; then
  echo "OK   $SKILL_DST"
else
  if [ -e "$SKILL_DST" ] && [ ! -L "$SKILL_DST" ]; then
    backup="$SKILL_DST.bak.$(date +%Y%m%d-%H%M%S)"
    echo "BACK $SKILL_DST -> $backup"
    mv "$SKILL_DST" "$backup"
  fi
  ln -sfn "$SKILL_SRC" "$SKILL_DST"
  echo "LINK $SKILL_DST -> $SKILL_SRC"
fi

# --- 2. DARKARCHON_HOME in shell rc ------------------------------------------

EXPORT_LINE="export DARKARCHON_HOME=$DARKARCHON"

case "${SHELL:-}" in
  */zsh)  RC="$HOME/.zshrc" ;;
  */bash) RC="$HOME/.bashrc" ;;
  *)      RC="" ;;
esac

if [ -z "$RC" ]; then
  echo "WARN unrecognized shell '${SHELL:-unset}' — add this to your rc yourself:"
  echo "     $EXPORT_LINE"
elif [ -f "$RC" ] && grep -q '^[^#]*\bDARKARCHON_HOME=' "$RC"; then
  echo "OK   DARKARCHON_HOME already set in $RC"
else
  printf '\n%s\n' "$EXPORT_LINE" >> "$RC"
  echo "RC   appended to $RC (open a new shell or 'source $RC')"
fi

echo
echo "Done. Optional next steps (see README): pip install --user mcp; dashboard setup."
