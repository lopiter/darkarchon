# Self-Install Instructions for Hermes

> **Usage**: paste this whole file into a new Hermes instance's chat.
> The agent installs the plugin into its own instance.

---

You are installing the **darkarchon-orchestrators** plugin into your own Hermes
instance. It lets you manage a staff of tmux-based Claude Code "orchestrator"
sessions. Follow these steps exactly, using your terminal and file tools.
Report the outcome of each step. If a step fails, stop and show the error —
do not improvise workarounds.

## Step 1 — Preconditions

Run and confirm all three succeed:

```bash
tmux -V
claude --version
git --version
```

If `claude` is missing, stop and tell the user to install/log in to Claude Code
first. (`tmux` missing → tell the user to install tmux.)

## Step 2 — Get the darkarchon checkout

```bash
DARKARCHON_HOME="${DARKARCHON_HOME:-$HOME/work/darkarchon}"
if [ -d "$DARKARCHON_HOME/hermes-plugin" ]; then
  git -C "$DARKARCHON_HOME" pull --ff-only
else
  git clone https://github.com/lopiter/darkarchon.git "$DARKARCHON_HOME"
fi
```

If the clone fails with an auth error, the repo is private — ask the user to
provide credentials or clone it themselves, then continue.

## Step 3 — Run the installer

```bash
DARKARCHON_HOME="${DARKARCHON_HOME:-$HOME/work/darkarchon}"
bash "$DARKARCHON_HOME/hermes-plugin/install.sh"
```

This symlinks the plugin into your `$HERMES_HOME/plugins/` and enables it in
your config. Read its output carefully — it prints reminders that matter for
the next step.

## Step 4 — Toolset visibility check

Read your own `~/.hermes/config.yaml` (or `$HERMES_HOME/config.yaml`).

- If there is **no** `platform_toolsets` section, or your platform's entry uses
  a composite like `[hermes-cli]` → nothing to do.
- If your platform (e.g. `cli`) has an **explicit list** such as
  `[file, terminal]` → append `orchestrator` to that list and save the file.
  Change nothing else in the config.

## Step 5 — Optional: Slack notifications

Ask the user whether they want Slack notifications (dispatch completions and
employee questions pushed to a Slack channel). If yes, ask them for their
Slack incoming-webhook URL (they create it at api.slack.com/apps → Incoming
Webhooks; never invent or reuse one you found elsewhere), then append to
their shell profile:

```bash
echo "export HERMES_ORCH_SLACK_WEBHOOK='<their URL>'" >> ~/.zshrc
```

This variable is read by the plugin only — it is not a Hermes core setting.
The same URL may be shared across machines; all of them will post to the
same channel. If they skip this, notifications still appear in the Hermes
conversation itself.

## Step 6 — Verify

```bash
hermes plugins list 2>/dev/null | grep -A2 darkarchon
```

Expected: `darkarchon-orchestrators` with status `enabled`.

## Step 7 — Report to the user

Tell the user, in their language:

1. Installation is complete, but the plugin loads on the **next Hermes
   restart** — they should restart this Hermes when convenient.
2. After restart, they can just ask in chat (e.g. *"hire a backend employee
   on ~/work/foo and have it do X"*). On first use you will ask them to name
   the fleet's tmux session — never pick that name yourself.
3. `/orch` shows the staff list, `/orch runs` recent tasks,
   `/orch team <name>` sets the fleet session name manually.
4. If this machine runs multiple Hermes instances, each needs its own
   `HERMES_HOME` (or `HERMES_ORCH_TEAM`) so fleets don't collide.

Do NOT spawn any orchestrator or set a team name during installation — that is
the user's decision after restart.
