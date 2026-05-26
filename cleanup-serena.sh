#!/usr/bin/env bash
# cleanup-serena.sh — Find and kill orphaned serena MCP process trees.
#
# Why: serena spawns a chain (uvx → python serena → kotlin-lsp.sh → java
# KotlinLanguageServer). When the parent Claude Code dies (worker killed,
# tmux session closed, crash), the chain often doesn't terminate cleanly
# on macOS. Java holds 150-200MB RSS each, so they accumulate.
#
# Strategy:
#   1. Find all uvx processes running serena (root of each tree)
#   2. For each root, check if its parent looks like an alive Claude Code
#   3. If parent is dead or non-Claude, kill the entire descendant tree
#
# Usage:
#   cleanup-serena.sh              # kill orphan trees only (safe default)
#   cleanup-serena.sh --dry-run    # show what would be killed, no action
#   cleanup-serena.sh --all        # nuke EVERY serena tree (live ones too)

set -u

DRY_RUN=0
ALL=0
for arg in "$@"; do
    case "$arg" in
        --dry-run|-n) DRY_RUN=1 ;;
        --all)        ALL=1 ;;
        -h|--help)
            sed -n '2,/^$/p' "$0" | sed 's/^# //;s/^#//'
            exit 0 ;;
    esac
done

# All serena uvx roots (bash 3 compatible — no mapfile)
serena_roots=()
while IFS= read -r line; do
    [ -n "$line" ] && serena_roots+=("$line")
done < <(pgrep -f 'uvx.*serena.*start-mcp-server' 2>/dev/null)

if [ ${#serena_roots[@]} -eq 0 ]; then
    echo "No serena uvx processes found."
    exit 0
fi

# Recursively collect all descendants of a PID
descendants() {
    local pid=$1
    local kids
    kids=$(pgrep -P "$pid" 2>/dev/null)
    for k in $kids; do
        echo "$k"
        descendants "$k"
    done
}

# Heuristic: parent is alive AND looks like Claude Code
parent_alive_and_claude() {
    local pid=$1
    local ppid
    ppid=$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')
    [ -z "$ppid" ] && return 1
    [ "$ppid" = "1" ] && return 1   # reparented to launchd → orphan
    local pcmd
    pcmd=$(ps -o command= -p "$ppid" 2>/dev/null)
    [ -z "$pcmd" ] && return 1
    # Match common Claude Code parent invocations:
    # - "claude --..." (bare command)
    # - "/path/to/claude --..." (absolute path)
    # - "claude-code", "node ... claude"
    echo "$pcmd" | grep -qE '(^|/|[[:space:]])claude([[:space:]]|$)|claude-code|node[[:space:]].*claude'
}

orphans=()
alive=()
for root in "${serena_roots[@]}"; do
    if [ "$ALL" = 1 ] || ! parent_alive_and_claude "$root"; then
        orphans+=("$root")
    else
        alive+=("$root")
    fi
done

echo "Serena uvx trees: total=${#serena_roots[@]} alive=${#alive[@]} orphan=${#orphans[@]}"

if [ ${#orphans[@]} -eq 0 ]; then
    echo "Nothing to clean up."
    exit 0
fi

# Build full PID list (orphan roots + all descendants)
kill_list=()
total_rss=0
for root in "${orphans[@]}"; do
    kill_list+=("$root")
    while IFS= read -r d; do
        kill_list+=("$d")
    done < <(descendants "$root")
done

# Show plan
echo
echo "Plan (PID, RSS-KB, command excerpt):"
for pid in "${kill_list[@]}"; do
    line=$(ps -o pid=,rss=,command= -p "$pid" 2>/dev/null | head -c 140)
    [ -n "$line" ] || continue
    rss=$(echo "$line" | awk '{print $2}')
    total_rss=$((total_rss + rss))
    echo "  $line"
done
echo "  -- total RSS to free: $((total_rss / 1024)) MB"

if [ "$DRY_RUN" = 1 ]; then
    echo
    echo "(dry-run — no processes killed)"
    exit 0
fi

echo
echo "Sending SIGTERM..."
for pid in "${kill_list[@]}"; do
    kill -TERM "$pid" 2>/dev/null
done
sleep 2

# Force-kill stragglers
straggler=0
for pid in "${kill_list[@]}"; do
    if kill -0 "$pid" 2>/dev/null; then
        straggler=$((straggler + 1))
        kill -KILL "$pid" 2>/dev/null
    fi
done
[ "$straggler" -gt 0 ] && echo "Force-killed $straggler straggler(s) with SIGKILL."

# Final report
freed=0
for pid in "${kill_list[@]}"; do
    kill -0 "$pid" 2>/dev/null || freed=$((freed + 1))
done
echo "Done: $freed/${#kill_list[@]} processes terminated."
