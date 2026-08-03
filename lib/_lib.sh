# Shared helpers sourced by every team script.
# Project-agnostic. Reads config.env from one level up + optional runtime registry.

# Resolve config.env (one level up from lib/)
TEAM_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ ! -f "$TEAM_ROOT/config.env" ]; then
    echo "ERROR: config.env not found at $TEAM_ROOT/config.env" >&2
    exit 1
fi
# shellcheck disable=SC1091
source "$TEAM_ROOT/config.env"

# A spawned worker inherits EE_STATE_DIR but NOT DARKARCHON_TEAM, and config.env
# rebuilds STATE_DIR from DARKARCHON_TEAM unconditionally — so every lib script a
# worker runs (mailbox.sh, ask.sh, and anything the MCP server delegates to)
# resolved the *default* team's state dir and wrote there. Its messages and
# questions landed somewhere its own orchestrator never looks.
#
# EE_STATE_DIR is stamped by the spawner and names the team this process actually
# belongs to, so it wins. An explicit DARKARCHON_TEAM still takes precedence:
# orchestrator-role workers are given one deliberately to namespace the team they
# manage, and in that case both agree anyway.
if [ -z "${DARKARCHON_TEAM:-}" ] && [ -n "${EE_STATE_DIR:-}" ]; then
    STATE_DIR="$EE_STATE_DIR"
    SESSION_NAME="$(basename "$EE_STATE_DIR")"
fi

# Export the resolved paths so python children (task_store.py, worker_state.py)
# read the same STATE_DIR without each reconstructing it from DARKARCHON_TEAM.
# HOST_STATE_DIR is the team dirs' parent and stays put even when EE_STATE_DIR
# repoints STATE_DIR at a nested worktree team.
export STATE_DIR TOOL_PREFIX HOST_STATE_DIR

# Per-host agent settings (hub URL, host id). Host-level, not team-level: the
# agent scans every tmux pane on the machine and reports them under a single
# HOST_ID, so one config and one running agent serve every team.
AGENT_CONFIG="$HOST_STATE_DIR/agent.config"

# migrate_agent_config — move a pre-existing team-level agent.config up to the
# host-level path. It used to be written to $STATE_DIR, so which team dir it
# landed in was decided by whatever DARKARCHON_TEAM the shell had on first
# start. Adopts the most recently modified copy and reports (without deleting)
# any others, which are now dead files.
migrate_agent_config() {
    [ -f "$AGENT_CONFIG" ] && return 0
    local newest="" f
    for f in "$HOST_STATE_DIR"/*/agent.config; do
        [ -f "$f" ] || continue
        if [ -z "$newest" ] || [ "$f" -nt "$newest" ]; then
            newest="$f"
        fi
    done
    [ -n "$newest" ] || return 0
    mkdir -p "$HOST_STATE_DIR"
    mv "$newest" "$AGENT_CONFIG"
    echo "migrated agent.config: $newest -> $AGENT_CONFIG"
    for f in "$HOST_STATE_DIR"/*/agent.config; do
        [ -f "$f" ] || continue
        echo "note: $f is no longer read (leftover team-level copy; safe to delete)"
    done
}

# Optional runtime worker registry (written by spawn-worker.sh, removed by kill-worker.sh)
RUNTIME_REGISTRY="$STATE_DIR/workers-runtime.env"
if [ -f "$RUNTIME_REGISTRY" ]; then
    # shellcheck disable=SC1091
    source "$RUNTIME_REGISTRY"
fi

# Sanitize a worker name for use as part of a bash variable identifier.
# bash variable names cannot contain '-' or other shell-special chars,
# so internally we map every non-alphanumeric character to '_'. The original
# (unsanitized) name is still used for tmux window names and user-facing IO.
safe_name() {
    printf '%s' "$1" | tr -c '[:alnum:]_' '_'
}

# worker_target <name>  -> echoes WORKER_<safe_name>_TARGET or empty
worker_target() {
    local sn
    sn="$(safe_name "$1")"
    local var="WORKER_${sn}_TARGET"
    echo "${!var:-}"
}

# worker_dir <name>
worker_dir() {
    local sn
    sn="$(safe_name "$1")"
    local var="WORKER_${sn}_DIR"
    echo "${!var:-}"
}

# worker_role <name>
worker_role() {
    local sn
    sn="$(safe_name "$1")"
    local var="WORKER_${sn}_ROLE"
    echo "${!var:-}"
}

# worker_kind <name>  -> echoes WORKER_<safe_name>_KIND, defaulting to "claude".
# Pre-existing registrations have no _KIND slot, so absence means claude — this
# keeps the claude dispatch/detection paths unchanged for legacy workers.
worker_kind() {
    local sn
    sn="$(safe_name "$1")"
    local var="WORKER_${sn}_KIND"
    echo "${!var:-claude}"
}

# worker_is_external <name>  -> exit 0 if EXTERNAL=1, else 1
worker_is_external() {
    local sn
    sn="$(safe_name "$1")"
    local var="WORKER_${sn}_EXTERNAL"
    [ "${!var:-0}" = "1" ]
}

# with_registry_lock <cmd> [<args>...]
# Serialize concurrent mutations of $STATE_DIR/workers-runtime.env and the
# adjacent orchestrator.txt. mkdir-based (atomic, portable to macOS bash 3.2
# without coreutils flock). 10s timeout; stale lock dirs surface as an
# obvious error so the user can rm -rf to recover.
#
# Pass either a function name or a regular command — both are exec'd by "$@".
with_registry_lock() {
    local lock_dir="$STATE_DIR/.registry.lock"
    local max_wait_sec="${LOCK_WAIT_SEC:-10}"
    local waited=0
    mkdir -p "$STATE_DIR"
    while ! mkdir "$lock_dir" 2>/dev/null; do
        if [ "$waited" -ge "$max_wait_sec" ]; then
            echo "ERROR: registry lock at $lock_dir held > ${max_wait_sec}s." >&2
            echo "       another spawn/kill in flight, or stale (rm -rf to recover)." >&2
            return 1
        fi
        sleep 1
        waited=$((waited + 1))
    done
    local rc=0
    "$@" || rc=$?
    rmdir "$lock_dir" 2>/dev/null || true
    return $rc
}

# io_dir — per-user scratch dir for dispatch prompt/result files. Replaces the
# world-accessible /tmp/ee. mode 700 so other users on a shared host can't read
# a worker's prompts. Path stays short (~/tmp/darkarchon-<uid>) to fit the
# 200-char tmux trigger budget. Prints the path; creates it if missing.
io_dir() {
    local d="/tmp/${TOOL_PREFIX:-darkarchon}-$(id -u)"
    mkdir -p "$d" 2>/dev/null || true
    chmod 700 "$d" 2>/dev/null || true
    printf '%s' "$d"
}

# with_named_lock <lock_name> <cmd...>
# Hold an exclusive lock for the duration of <cmd> so two concurrent dispatches
# to the same worker (or same cwd) can't both pass a busy-check and double-fire.
# mkdir-atomic (same portability rationale as with_registry_lock). The lock dir
# records the owner pid; a lock held by a DEAD pid is stolen (crashed dispatch
# left it behind). If a LIVE owner holds it past DISPATCH_LOCK_WAIT_SEC, returns
# 99 so the caller can report "another dispatch in flight" rather than block.
with_named_lock() {
    local name="$1"; shift
    local lock_dir="$STATE_DIR/locks/${name}.lock"
    local pidfile="$lock_dir/pid"
    local max_wait="${DISPATCH_LOCK_WAIT_SEC:-3}"
    local waited=0
    mkdir -p "$STATE_DIR/locks"
    while ! mkdir "$lock_dir" 2>/dev/null; do
        local owner=""
        owner="$(cat "$pidfile" 2>/dev/null || true)"
        if [ -n "$owner" ] && ! kill -0 "$owner" 2>/dev/null; then
            # stale — owner pid gone. Steal it (mkdir below re-establishes atomically).
            rm -rf "$lock_dir" 2>/dev/null || true
            continue
        fi
        if [ "$waited" -ge "$max_wait" ]; then
            return 99
        fi
        sleep 1
        waited=$((waited + 1))
    done
    echo "$$" > "$pidfile" 2>/dev/null || true
    local rc=0
    "$@" || rc=$?
    rm -rf "$lock_dir" 2>/dev/null || true
    return $rc
}

# all_known_workers — print every name with a defined WORKER_<sn>_TARGET.
# Note this prints the SANITIZED form (the registry key); the human-readable
# name lives in the matching _NAME slot when set, else falls back to the key.
all_known_workers() {
    local v
    for v in $(compgen -v 2>/dev/null | grep -E '^WORKER_[A-Za-z0-9_]+_TARGET$'); do
        local sn="${v#WORKER_}"
        sn="${sn%_TARGET}"
        local name_var="WORKER_${sn}_NAME"
        echo "${!name_var:-$sn}"
    done | sort -u
}

# workers_sharing_dir <dir> [<exclude_name>] — print names of registered workers
# whose cwd (WORKER_<sn>_DIR) equals <dir>, excluding <exclude_name> if given.
# Used for cwd-collision warnings (spawn/invite) and dispatch serialization
# (dispatch-safe refuses when a same-cwd peer is busy) so a claude and a codex
# worker on the same repo don't edit the working tree concurrently.
workers_sharing_dir() {
    local target_dir="$1"
    local exclude="${2:-}"
    [ -z "$target_dir" ] && return 0
    local exclude_sn=""
    [ -n "$exclude" ] && exclude_sn="$(safe_name "$exclude")"
    local v
    for v in $(compgen -v 2>/dev/null | grep -E '^WORKER_[A-Za-z0-9_]+_DIR$'); do
        local sn="${v#WORKER_}"
        sn="${sn%_DIR}"
        [ -n "$exclude_sn" ] && [ "$sn" = "$exclude_sn" ] && continue
        if [ "${!v:-}" = "$target_dir" ]; then
            local name_var="WORKER_${sn}_NAME"
            echo "${!name_var:-$sn}"
        fi
    done | sort -u
}
