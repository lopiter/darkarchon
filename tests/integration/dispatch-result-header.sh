#!/usr/bin/env bash
# Integration harness for lib/dispatch.sh attempt separation.
# Real tmux window + real STATE_DIR; the "worker" is us — we drive its hook state
# file and write result files by hand, so dispatch.sh's poll loop runs for real.
set -uo pipefail
DA="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SESS="dispatch-it-$$"
# config.env derives STATE_DIR from DARKARCHON_TEAM, so that is the isolation knob.
export DARKARCHON_TEAM="it-$$"
export POLL_INTERVAL=1 TASK_IDLE_CONFIRM=2 TASK_NOSTART_CONFIRM=3 TASK_MAX_SECONDS=45
SD="$HOME/.darkarchon/$DARKARCHON_TEAM"
IO="/tmp/darkarchon-$(id -u)"        # shared with real dispatches — never rm -rf
mkdir -p "$SD"/{states,heartbeats,locks}

tmux new-session -d -s "$SESS" -n w1 -c /tmp 2>/dev/null
printf 'WORKER_w1_NAME="w1"\nWORKER_w1_TARGET="%s:w1"\nWORKER_w1_DIR="/tmp"\nWORKER_w1_ROLE="worker"\nWORKER_w1_KIND="claude"\n' "$SESS" > "$SD/workers-runtime.env"

hook(){ printf '{"state":"%s","detail":"","event":"t","ts_epoch":%s}\n' "$1" "$(date +%s)" > "$SD/states/w1.json"; }
beat(){ printf '{"worker":"w1","pid":%s,"last_seen_epoch":%s}\n' "$$" "$(date +%s)" > "$SD/heartbeats/w1.json"; }
( while :; do beat; sleep 2; done ) & BEAT=$!
disown $BEAT 2>/dev/null || true
MINE=()                              # only clean up files we created
cleanup(){ kill "$BEAT" 2>/dev/null; tmux kill-session -t "=$SESS" 2>/dev/null
           rm -rf "$SD"; for f in "${MINE[@]:-}"; do [ -n "$f" ] && rm -f "$f"; done; }
trap cleanup EXIT
beat

pass=0; fail=0
ok(){ echo "  PASS: $1"; pass=$((pass+1)); }
no(){ echo "  FAIL: $1"; fail=$((fail+1)); }
# dispatch.sh creates the PROMPT file; the result file is the worker's job, so
# derive its name from the prompt rather than waiting for it to appear.
tid(){ basename "$(ls -t "$IO"/p-*.txt 2>/dev/null | head -1)" .txt | sed 's/^p-//'; }
rpath(){ echo "$IO/r-$(tid)-a$1.txt"; }
marker(){ echo "DONE-$(tid)-a$1"; }

# ── T1: correct header is stripped ──────────────────────────────────────────
echo "T1: header stripped from result"
hook busy
( sleep 3; printf '%s\nthe answer\n' "$(marker 1)" > "$(rpath 1)" ) &
OUT=$(bash "$DA/lib/dispatch.sh" w1 "do it" 2>/tmp/t1.err); RC=$?
MINE+=("$(rpath 1)")
[ "$RC" -eq 0 ] && ok "exit 0" || no "exit $RC — $(tail -2 /tmp/t1.err|tr '\n' ' ')"
[ "$OUT" = "the answer" ] && ok "header stripped, body only" || no "got $(printf %q "$OUT")"
grep -q HEADER_MISMATCH /tmp/t1.err && no "unexpected mismatch warning" || ok "no mismatch warning"

# ── T2: missing header ⇒ warn, still accept ─────────────────────────────────
echo "T2: missing header warns but succeeds"
hook busy
( sleep 3; printf 'bare answer\n' > "$(rpath 1)" ) &
OUT=$(bash "$DA/lib/dispatch.sh" w1 "do it" 2>/tmp/t2.err); RC=$?
MINE+=("$(rpath 1)")
[ "$RC" -eq 0 ] && ok "exit 0" || no "exit $RC"
[ "$OUT" = "bare answer" ] && ok "result preserved intact" || no "got $(printf %q "$OUT")"
grep -q HEADER_MISMATCH /tmp/t2.err && ok "warned" || no "no warning emitted"

# ── T3: strict mode rejects ─────────────────────────────────────────────────
echo "T3: STRICT_RESULT_HEADER=1 rejects bad header"
hook busy
( sleep 3; printf 'bare\n' > "$(rpath 1)" ) &
STRICT_RESULT_HEADER=1 bash "$DA/lib/dispatch.sh" w1 "do it" >/dev/null 2>/tmp/t3.err; RC=$?
MINE+=("$(rpath 1)")
[ "$RC" -eq 3 ] && ok "exit 3" || no "exit $RC"
grep -q "strict mode" /tmp/t3.err && ok "strict message" || no "no strict message"

echo "--- $pass passed, $fail failed ---"
[ "$fail" -eq 0 ]
