#!/usr/bin/env bash
# dispatch-safe.sh: --after dependency gating and the consecutive-failure
# circuit breaker. Both refuse before any trigger is sent, so no real worker is
# needed — a tmux session with an idle pane is enough to pass the liveness check.
set -uo pipefail
DA="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export DARKARCHON_TEAM="depit-$$"
SD="$HOME/.darkarchon/$DARKARCHON_TEAM"; SESS="depit-$$"
mkdir -p "$SD"/{states,heartbeats,locks}
tmux new-session -d -s "$SESS" -n w -c /tmp 2>/dev/null
cleanup(){ tmux kill-session -t "=$SESS" 2>/dev/null; rm -rf "$SD"; }
trap cleanup EXIT
# Two workers: the breaker section needs a failure history of its own, and the
# dependency section's rows would otherwise be counted into it.
printf 'WORKER_w_NAME="w"\nWORKER_w_TARGET="%s:w"\nWORKER_w_DIR="/tmp/dep"\nWORKER_cbw_NAME="cbw"\nWORKER_cbw_TARGET="%s:w"\nWORKER_cbw_DIR="/tmp/cb"\n' "$SESS" "$SESS" > "$SD/workers-runtime.env"

# A pass-through dispatch reaches a bare shell that never writes a result. Keep
# that cheap: give up in seconds rather than the default hour.
export POLL_INTERVAL=1 TASK_IDLE_CONFIRM=2 TASK_NOSTART_CONFIRM=2 TASK_MAX_SECONDS=20

TS=("python3" "$DA/lib/task_store.py")
DS="$DA/dispatch-safe.sh"
p=0; f=0; ok(){ echo "  PASS: $1"; p=$((p+1)); }; no(){ echo "  FAIL: $1"; f=$((f+1)); }

settle() {  # settle <id> <final-status> <dispatched_at> [worker]
    "${TS[@]}" insert <<EOF >/dev/null
{"id":"$1","worker":"${4:-w}","tmux_target":"$SESS:w","prompt":"p","dispatched_at":"$3"}
EOF
    "${TS[@]}" update-status "$1" running >/dev/null
    [ "$2" != "running" ] && "${TS[@]}" update-status "$1" "$2" >/dev/null
    return 0
}

echo "--after dependency gating:"
# A dependency that does not exist must fail fast, not wait out the deadline.
start=$(date +%s)
DEPS_WAIT_SEC=30 "$DS" --after nosuchid w "task" >/dev/null 2>&1
rc=$?; elapsed=$(( $(date +%s) - start ))
[ "$rc" -eq 16 ] && ok "missing dependency -> exit 16" || no "missing dependency exit $rc"
[ "$elapsed" -lt 10 ] && ok "missing dependency refused immediately (${elapsed}s)" || no "waited ${elapsed}s for a missing id"

settle dep-fail failed "2026-07-29T01:00:00Z"
DEPS_WAIT_SEC=30 "$DS" --after dep-fail w "task" >/dev/null 2>&1
[ $? -eq 16 ] && ok "failed dependency -> exit 16" || no "failed dependency exit code"

settle dep-cancel cancelled "2026-07-29T01:00:00Z"
DEPS_WAIT_SEC=30 "$DS" --after dep-cancel w "task" >/dev/null 2>&1
[ $? -eq 16 ] && ok "cancelled dependency -> exit 16" || no "cancelled dependency exit code"

# Still running when the deadline passes ⇒ 17, distinct from a hard failure.
settle dep-slow running "2026-07-29T01:00:00Z"
start=$(date +%s)
DEPS_WAIT_SEC=4 DEPS_POLL_INTERVAL=1 "$DS" --after dep-slow w "task" >/dev/null 2>&1
rc=$?; elapsed=$(( $(date +%s) - start ))
[ "$rc" -eq 17 ] && ok "unfinished dependency times out -> exit 17" || no "timeout exit $rc"
[ "$elapsed" -ge 3 ] && ok "waited for the deadline (${elapsed}s)" || no "returned too early (${elapsed}s)"

echo "Circuit breaker:"
# Counting first, before any dispatch that could add rows of its own.
for i in 1 2 3; do settle "cb$i" failed "2026-07-29T0$i:00:00Z" cbw; done
[ "$("${TS[@]}" consecutive-failures cbw)" = "3" ] && ok "3 consecutive failures counted" || no "failure count: $("${TS[@]}" consecutive-failures cbw)"
settle cb-mixed cancelled "2026-07-29T04:00:00Z" cbw
[ "$("${TS[@]}" consecutive-failures cbw)" = "3" ] && ok "a cancelled task neither counts nor masks" || no "cancelled changed the count"
settle cb-ok completed "2026-07-29T05:00:00Z" cbw
[ "$("${TS[@]}" consecutive-failures cbw)" = "0" ] && ok "a success resets the streak" || no "streak not reset"

# Now the refusal behaviour, from a known-bad history. These must be strictly
# newer than cb-ok above, or the success would still be terminating the streak.
for i in 6 7 8; do settle "cb$i" failed "2026-07-29T0$i:00:00Z" cbw; done
OUT=$("$DS" cbw "task" 2>&1 >/dev/null); rc=$?
[ "$rc" -eq 15 ] && ok "breaker refuses -> exit 15" || no "breaker exit $rc"
echo "$OUT" | grep -q "^REFUSED:" && ok "refusal keeps the REFUSED: prefix hermes greps for" || no "no REFUSED: prefix"

# --force and a raised threshold both bypass it. These dispatch for real against
# a bare shell, so they end in NO_RESULT (exit 3) — the point is only that the
# breaker did not stop them at 15.
CIRCUIT_THRESHOLD=99 "$DS" cbw "task" >/dev/null 2>&1; rc=$?
[ "$rc" -ne 15 ] && ok "raising CIRCUIT_THRESHOLD lets it through (exit $rc)" || no "threshold ignored"
"$DS" --force cbw "task" >/dev/null 2>&1; rc=$?
[ "$rc" -ne 15 ] && ok "--force overrides the breaker (exit $rc)" || no "--force did not override"

echo "--- $p passed, $f failed ---"
[ "$f" -eq 0 ]
