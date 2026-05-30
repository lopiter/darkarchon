#!/usr/bin/env bash
# dispatch-safe.sh — Dispatch only when the worker (and any same-cwd peer) is idle.
#
# Pre-flight checks before delegating to lib/dispatch.sh:
#   1. Target worker busy? (kind-aware: Claude spinner vs codex "Working (…)" line)
#   2. codex worker showing an auth/stream error? (can't make progress → refuse)
#   3. claude worker has typed-but-unsent user input on the prompt line?
#   4. A *different* worker sharing the same cwd is busy? (serialize edits so a
#      claude and a codex worker on one repo don't race on the git working tree)
#
# Usage (drop-in replacement for lib/dispatch.sh):
#   dispatch-safe.sh [--force] <worker> <prompt...>
#   echo 'long prompt' | dispatch-safe.sh [--force] <worker> -
#
# --force: skip Check 3 AND, for claude workers, blast BSpaces to wipe the
# prompt line before sending the trigger. Use when you know the "typed" text
# is not real user input (e.g. Claude Code's recap-suggested next prompt or
# autocomplete ghost text that the dim-style heuristic missed). Trade-off:
# real user-typed input would be clobbered — caller's responsibility.
#
# Exit codes:
#   0      success — dispatched and completed (lib/dispatch.sh exit code)
#   10     refused — worker busy (won't dispatch, user must wait or interrupt)
#   11     refused — pane content shows possible user-typed input pending (claude;
#                     suppressed by --force)
#   12     refused — codex worker shows auth/stream error (run `codex login`)
#   13     refused — a same-cwd peer worker is busy (serialized to protect the tree)
#   1/2/3  passthrough from lib/dispatch.sh (bad args / timeout / parse error)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/lib/_lib.sh"

# Optional leading --force: skip the claude typed-input check (Check 3) AND
# blast BSpaces to wipe any ghost text / autocomplete on the prompt line before
# delegating. See header comment for the trade-off.
FORCE=0
while [ $# -gt 0 ]; do
    case "$1" in
        --force|--force-clear) FORCE=1; shift ;;
        --) shift; break ;;
        -*) echo "ERROR: unknown option '$1'" >&2; exit 1 ;;
        *) break ;;
    esac
done

if [ $# -lt 2 ]; then
    echo "Usage: $0 [--force] <worker> <prompt...>" >&2
    echo "       echo 'long prompt' | $0 [--force] <worker> -" >&2
    exit 1
fi

WORKER="$1"

# Resolve target
TARGET="$(worker_target "$WORKER")"
if [ -z "$TARGET" ]; then
    echo "ERROR: unknown worker '$WORKER'" >&2
    echo "Known: $(all_known_workers | tr '\n' ' ')" >&2
    exit 1
fi
if ! tmux has-session -t "=${TARGET%%:*}" 2>/dev/null; then
    echo "ERROR: tmux session '${TARGET%%:*}' not running" >&2
    exit 1
fi

KIND="$(worker_kind "$WORKER")"

# ─── Detection patterns (per kind) ──────────────────────────────────────────
# Claude active-state markers (only present DURING current work): the localized
# gerund + ellipsis ("Whisking…", "작성 중 …"), or "still running". We do NOT gate
# on the spinner glyph: Claude Code cycles through many glyphs (· ✢ ✳ ✶ ✽ ✻ ★ …),
# so requiring a fixed set false-negatives whenever the captured frame shows a
# different glyph (e.g. "· Meandering…") — that misclassifies a busy worker as
# idle. The trailing … (U+2026, not "...") keeps this off non-spinner text.
# Mirrors detectors/claude.py's BUSY_PATTERN.
CLAUDE_ACTIVE_PATTERN='([A-Za-z]+(ing|ling|ning)|중[[:space:]]*)…|still running'
# Codex busy marker: the "Working (<N>s • Esc to interrupt)" status line. ASCII
# substrings only (codex composer/footer glyphs ▌ ⏎ vary; don't rely on them).
CODEX_BUSY_PATTERN='Working[[:space:]]*\([0-9]+[[:space:]]*s|[Ee]sc to interrupt'
# Codex auth/stream failure: token expired or stream error → can't progress.
CODEX_AUTH_PATTERN='Failed to refresh token|401 Unauthorized|stream error'

# pane_status <target> <kind> → prints one of: "busy" | "auth" | "" (idle/unknown).
# Captures only the visible screen (no -S); codex uses the alternate screen and
# claude's status line is at the bottom either way.
pane_status() {
    local target="$1" kind="$2" pane tail
    pane="$(tmux capture-pane -p -t "=$target" 2>/dev/null || true)"
    [ -z "$pane" ] && { echo ""; return 0; }
    tail="$(printf '%s\n' "$pane" | grep -v '^[[:space:]]*$' | tail -15)"
    if [ "$kind" = "codex" ]; then
        if echo "$tail" | grep -qE "$CODEX_AUTH_PATTERN"; then echo "auth"; return 0; fi
        if echo "$tail" | grep -qE "$CODEX_BUSY_PATTERN"; then echo "busy"; return 0; fi
    else
        if echo "$tail" | grep -qE "$CLAUDE_ACTIVE_PATTERN"; then echo "busy"; return 0; fi
    fi
    echo ""
}

# ─── Check 1/2: target worker self-state ────────────────────────────────────
PANE_TAIL="$(tmux capture-pane -p -t "=$TARGET" 2>/dev/null || true)"
if [ -z "$PANE_TAIL" ]; then
    echo "ERROR: failed to capture pane $TARGET (worker not running?)" >&2
    exit 1
fi
PANE_TAIL="$(printf '%s\n' "$PANE_TAIL" | grep -v '^[[:space:]]*$' | tail -15)"

SELF_STATUS="$(pane_status "$TARGET" "$KIND")"
if [ "$SELF_STATUS" = "auth" ]; then
    echo "REFUSED: codex worker '$WORKER' ($TARGET) shows an auth/stream error." >&2
    echo "  recent pane (last 5 lines):" >&2
    echo "$PANE_TAIL" | tail -5 | sed 's/^/    /' >&2
    echo >&2
    echo "  codex 토큰이 만료/미로그인 상태로 보입니다. 'codex login' (또는 OPENAI_API_KEY) 후 다시 시도해주세요." >&2
    exit 12
fi
if [ "$SELF_STATUS" = "busy" ]; then
    if [ "$KIND" = "codex" ]; then
        matched=$(echo "$PANE_TAIL" | grep -oE "$CODEX_BUSY_PATTERN" | sort -u | tr '\n' ',' | sed 's/,$//')
    else
        matched=$(echo "$PANE_TAIL" | grep -oE "$CLAUDE_ACTIVE_PATTERN" | sort -u | tr '\n' ',' | sed 's/,$//')
    fi
    echo "REFUSED: worker '$WORKER' ($TARGET) appears actively processing." >&2
    echo "  matched markers: $matched" >&2
    echo "  recent pane (last 5 lines):" >&2
    echo "$PANE_TAIL" | tail -5 | sed 's/^/    /' >&2
    echo >&2
    echo "  Wait for it to finish, OR if user is directly chatting with this worker," >&2
    echo "  ask them to detach first. Then retry." >&2
    exit 10
fi

# ─── Check 3: typed-but-unsent user input (claude only) ─────────────────────
# Codex keeps its composer (▌) and footer visible at all times and uses a
# different prompt structure, so the ❯-based heuristic doesn't apply — skip it.
# --force skips this check entirely (caller asserted the text isn't real input).
if [ "$KIND" != "codex" ] && [ "$FORCE" -eq 0 ]; then
    # Capture WITH escape codes (-e) so we can distinguish placeholder/autocomplete
    # (rendered with \x1b[7m reverse + \x1b[2m dim) from real user typing (plain).
    PANE_E="$(tmux capture-pane -e -p -t "=$TARGET" -S -8 2>/dev/null | tail -8 || true)"
    # Find prompt line (contains ❯ / U+276F)
    PROMPT_LINE="$(echo "$PANE_E" | grep -F $'\xe2\x9d\xaf' | tail -1 || true)"

    if [ -n "$PROMPT_LINE" ]; then
        # Strip everything up to and including "❯ " (prompt + one space)
        AFTER_PROMPT="${PROMPT_LINE#*$'\xe2\x9d\xaf'}"
        AFTER_PROMPT="${AFTER_PROMPT# }"
        # Plain content (no escape codes)
        PLAIN="$(printf '%s' "$AFTER_PROMPT" | sed -E $'s/\x1b\\[[0-9;]*m//g')"
        # Trim whitespace
        PLAIN_TRIMMED="$(printf '%s' "$PLAIN" | tr -d '[:space:]')"

        if [ -n "$PLAIN_TRIMMED" ]; then
            # Content present. Distinguish placeholder vs user input:
            #   - placeholder/autocomplete: rendered with \x1b[2m (dim)
            #   - user typed text: NO dim styling.
            if printf '%s' "$AFTER_PROMPT" | grep -qE $'\x1b\\[(2|0;2)m'; then
                # Dim styling present — placeholder/autocomplete → treat as idle
                :
            else
                echo "REFUSED: worker '$WORKER' ($TARGET) prompt line has unsent user input." >&2
                echo "  prompt content: $PLAIN" >&2
                echo >&2
                echo "  사용자가 워커에 직접 입력 중인 것으로 보입니다. Enter 로 보내거나 지운 뒤 다시 시도해주세요." >&2
                exit 11
            fi
        fi
    fi
fi

# ─── Check 4: same-cwd peer serialization ───────────────────────────────────
# If a *different* worker shares this worker's cwd and is currently busy, refuse:
# two agents editing one git working tree concurrently corrupts each other's
# edits (.git/index.lock contention, half-applied changes). Serializing here is
# the runtime half of the cwd guard (spawn/invite emit a warning at registration).
SELF_DIR="$(worker_dir "$WORKER")"
if [ -n "$SELF_DIR" ]; then
    while IFS= read -r PEER; do
        [ -z "$PEER" ] && continue
        PEER_TARGET="$(worker_target "$PEER")"
        [ -z "$PEER_TARGET" ] && continue
        tmux has-session -t "=${PEER_TARGET%%:*}" 2>/dev/null || continue
        PEER_KIND="$(worker_kind "$PEER")"
        if [ "$(pane_status "$PEER_TARGET" "$PEER_KIND")" = "busy" ]; then
            echo "REFUSED: peer worker '$PEER' ($PEER_TARGET) shares cwd and is busy." >&2
            echo "  cwd: $SELF_DIR" >&2
            echo >&2
            echo "  같은 repo를 쓰는 다른 워커가 작업 중입니다. git 워킹트리 충돌을 막기 위해" >&2
            echo "  dispatch 를 직렬화합니다 — '$PEER' 가 끝난 뒤 다시 시도해주세요." >&2
            exit 13
        fi
    done < <(workers_sharing_dir "$SELF_DIR" "$WORKER")
fi

# --force pre-clear (claude only): blast BSpaces to wipe any ghost text /
# autocomplete on the prompt line before the trigger is sent. We do NOT verify
# via capture-pane afterwards — Claude Code TUI rendering vs. tmux's capture
# snapshot can lag for seconds, so a single-capture verify deadlocks orchestrators
# that read it. Trust the keystrokes: 200 BSpaces erase any realistic prompt
# input; an empty prompt swallows extra BSpaces harmlessly. C-u/C-k first as a
# readline-style fast-clear that some Claude Code versions honor.
if [ "$FORCE" -eq 1 ] && [ "$KIND" != "codex" ]; then
    tmux send-keys -t "=$TARGET" C-u C-k 2>/dev/null || true
    BSP_ARGS=""
    for _ in $(seq 1 200); do BSP_ARGS="$BSP_ARGS BSpace"; done
    # shellcheck disable=SC2086
    tmux send-keys -t "=$TARGET" $BSP_ARGS 2>/dev/null || true
    sleep 0.4   # let the TUI process the burst before dispatch sends the trigger
fi

# Idle — delegate to real dispatch.sh
exec "$HERE/lib/dispatch.sh" "$@"
