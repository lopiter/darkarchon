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
