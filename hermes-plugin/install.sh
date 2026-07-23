#!/usr/bin/env bash
# Install the darkarchon-orchestrators plugin into a Hermes instance.
#
# Usage:
#   ./install.sh                  # install into ${HERMES_HOME:-~/.hermes}
#   HERMES_HOME=~/.hermes/profiles/work ./install.sh   # a specific profile
#
# Idempotent: re-running refreshes the symlink and re-enables the plugin.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_SRC="$HERE/darkarchon-orchestrators"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PLUGINS_DIR="$HERMES_HOME/plugins"

if [ ! -f "$PLUGIN_SRC/plugin.yaml" ]; then
    echo "ERROR: plugin source not found at $PLUGIN_SRC" >&2
    exit 1
fi
if [ ! -d "$HERMES_HOME" ]; then
    echo "ERROR: HERMES_HOME '$HERMES_HOME' does not exist — is Hermes installed" \
         "(or the profile initialized) on this machine?" >&2
    exit 1
fi

mkdir -p "$PLUGINS_DIR"
ln -sfn "$PLUGIN_SRC" "$PLUGINS_DIR/darkarchon-orchestrators"
echo "Linked: $PLUGINS_DIR/darkarchon-orchestrators -> $PLUGIN_SRC"

# Enable via the hermes CLI when available (adds to plugins.enabled in
# config.yaml); otherwise tell the user what to add by hand.
if command -v hermes >/dev/null 2>&1; then
    HERMES_HOME="$HERMES_HOME" hermes plugins enable darkarchon-orchestrators \
        && echo "Enabled via 'hermes plugins enable'." \
        || echo "WARNING: 'hermes plugins enable' failed — add 'darkarchon-orchestrators' under plugins.enabled in $HERMES_HOME/config.yaml manually." >&2
else
    echo "NOTE: hermes CLI not on PATH. Add to $HERMES_HOME/config.yaml:"
    echo "  plugins:"
    echo "    enabled:"
    echo "      - darkarchon-orchestrators"
fi

cat <<EOF

Done. Reminders:
- If config.yaml keeps an EXPLICIT platform_toolsets list (e.g. cli: [file, terminal]),
  add 'orchestrator' to it — otherwise the toolset is filtered out. If the list is
  unset (default hermes-cli composite), nothing to do.
- Requirements on this machine: tmux, claude CLI, and this darkarchon checkout
  (export DARKARCHON_HOME=$(cd "$HERE/.." && pwd) if it is not ~/work/darkarchon).
- Restart Hermes to load the plugin. On first use it will ask the user to name
  the fleet's tmux session (per-HERMES_HOME; HERMES_ORCH_TEAM env overrides).
- Optional Slack notifications (completions + employee questions): export
  HERMES_ORCH_SLACK_WEBHOOK=<Slack incoming-webhook URL> in your shell profile.
  Plugin-only variable; unset = disabled; the same URL works on every machine.
- Running several Hermes instances on ONE machine: give each its own
  HERMES_HOME (or HERMES_ORCH_TEAM) so each manager gets its own fleet —
  two managers sharing a fleet will race on the same orchestrators.
EOF
