#!/usr/bin/env bash
# teams.sh — inspect team state dirs and archive the ones nobody uses.
#
#   teams.sh list [--json]        grade every team by last activity
#   teams.sh archive <team>...    move specific teams out of the way
#   teams.sh archive --stale      move every team graded 'stale'
#   teams.sh archive --inactive   move every team with nothing running
#
# Archiving MOVES a state dir to $HOME/.<tool>-archive/<date>/ — it never
# deletes. tasks.db carries a team's whole dispatch history, so an archived team
# can be restored by moving the directory back.
#
# Reads state dirs directly, so it works whether or not a hub is running.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/_lib.sh"  # provides HOST_STATE_DIR, STATE_DIR, TEAM_*_DAYS

ARCHIVE_ROOT="$HOME/.${TOOL_PREFIX}-archive"
CLI=(python3 "$HERE/teams_cli.py")
THRESHOLDS=(--root "$HOST_STATE_DIR"
            --dormant-days "$TEAM_DORMANT_DAYS"
            --stale-days "$TEAM_STALE_DAYS")

usage() {
    sed -n '2,13p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit "${1:-1}"
}

# A team is off-limits while any tmux session it registered still exists. The
# check is deliberately blunt — a surviving session means panes may still be
# reading and writing in there, and refusing costs nothing.
_live_sessions_for() {
    local dir="$1" reg="$1/workers-runtime.env"
    [ -f "$reg" ] || return 0
    local sessions
    sessions="$(sed -n 's/^WORKER_[A-Za-z0-9_]*_TARGET=//p' "$reg" \
                | tr -d "\"'" | cut -d: -f1 | sort -u)"
    local s
    for s in $sessions; do
        if tmux has-session -t "=$s" 2>/dev/null; then
            echo "$s"
        fi
    done
}

# Refuse anything we cannot prove is safe to move. Prints the reason and returns
# 1 so callers can skip a team without aborting a whole batch.
_check_archivable() {
    local dir="$1" name="$2"
    if [ ! -d "$dir" ]; then
        echo "  skip $name: no such state dir ($dir)" >&2
        return 1
    fi
    if [ "$dir" = "$STATE_DIR" ]; then
        echo "  skip $name: this shell's own team (DARKARCHON_TEAM=$SESSION_NAME)" >&2
        return 1
    fi
    local live
    live="$(_live_sessions_for "$dir" | tr '\n' ' ')"
    if [ -n "${live// /}" ]; then
        echo "  skip $name: tmux session still alive ($live)" >&2
        return 1
    fi
    return 0
}

# Echoes the destination on success, nothing on skip, so the caller can count
# what actually moved. Creates the archive dir only when there is something to
# put in it — a run that skips everything must not litter an empty dated dir.
_archive_one() {
    local dir="$1" name="$2" dest_root="$3"
    _check_archivable "$dir" "$name" || return 0
    local rel="${dir#"$HOST_STATE_DIR"/}"
    local dest="$dest_root/$rel"
    if [ -e "$dest" ]; then
        echo "  skip $name: $dest already exists" >&2
        return 0
    fi
    mkdir -p "$(dirname "$dest")"
    mv "$dir" "$dest"
    echo "  archived $name -> $dest" >&2
    echo "$dest"
}

cmd="${1:-list}"
shift || true

case "$cmd" in
    list)
        "${CLI[@]}" list "${THRESHOLDS[@]}" "$@"
        ;;
    archive)
        assume_yes=0
        targets=()
        want_stale=0
        want_inactive=0
        for arg in "$@"; do
            case "$arg" in
                --yes|-y)   assume_yes=1 ;;
                --stale)    want_stale=1 ;;
                --inactive) want_inactive=1 ;;
                -*)         echo "unknown flag: $arg" >&2; usage ;;
                *)          targets+=("$arg") ;;
            esac
        done

        dirs=()
        # --inactive is the wider net: every team with nothing running, which is
        # what the dashboard lists under "inactive teams". --stale is the subset
        # that has also been quiet past TEAM_STALE_DAYS.
        if [ "$want_inactive" = "1" ]; then
            while IFS= read -r d; do
                [ -n "$d" ] && dirs+=("$d")
            done < <("${CLI[@]}" select "${THRESHOLDS[@]}" --inactive)
        elif [ "$want_stale" = "1" ]; then
            while IFS= read -r d; do
                [ -n "$d" ] && dirs+=("$d")
            done < <("${CLI[@]}" select "${THRESHOLDS[@]}" --tier stale)
        fi
        # A target may be a team name, a path relative to the state root, or an
        # absolute state dir. The last two matter for worktree teams: `feature-x`
        # nested under `myteam` is NAMED `myteam-feature-x` but LIVES at
        # `myteam/feature-x`, so a name alone cannot locate it. The dashboard
        # copies the absolute path for exactly this reason.
        for t in "${targets[@]:-}"; do
            [ -z "$t" ] && continue
            case "$t" in
                /*) dirs+=("$t") ;;
                *)  dirs+=("$HOST_STATE_DIR/$t") ;;
            esac
        done

        if [ "${#dirs[@]}" -eq 0 ]; then
            echo "nothing to archive."
            [ "$want_inactive" = "1" ] && echo "(every team has a worker running)"
            [ "$want_stale" = "1" ] && echo "(no teams graded 'stale' at >${TEAM_STALE_DAYS}d)"
            exit 0
        fi

        echo "About to archive ${#dirs[@]} team(s) into $ARCHIVE_ROOT/$(date +%Y%m%d)/:"
        for d in "${dirs[@]}"; do
            echo "  - ${d#"$HOST_STATE_DIR"/}"
        done
        echo "This MOVES them (including tasks.db history). Nothing is deleted."
        if [ "$assume_yes" != "1" ]; then
            if [ ! -t 0 ]; then
                echo "ERROR: refusing to archive non-interactively without --yes." >&2
                exit 1
            fi
            read -r -p "Proceed? [y/N] " reply
            case "$reply" in
                y|Y|yes|YES) ;;
                *) echo "aborted."; exit 0 ;;
            esac
        fi

        dest_root="$ARCHIVE_ROOT/$(date +%Y%m%d)"
        moved=0
        for d in "${dirs[@]}"; do
            if [ -n "$(_archive_one "$d" "${d#"$HOST_STATE_DIR"/}" "$dest_root")" ]; then
                moved=$((moved + 1))
            fi
        done
        echo
        if [ "$moved" -eq 0 ]; then
            echo "nothing moved."
        else
            echo "moved $moved team(s). restore with: mv $dest_root/<team> $HOST_STATE_DIR/"
        fi
        ;;
    -h|--help|help)
        usage 0
        ;;
    *)
        echo "unknown command: $cmd" >&2
        usage
        ;;
esac
