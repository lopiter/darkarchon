#!/usr/bin/env bash
# The race this commit exists to fix: a nudge issues attempt 2 while attempt 1 is
# still alive; attempt 1 then finishes late. Its answer must NOT be served as
# attempt 2's result.
#
# io_dir is shared with the user's real dispatches, so this identifies its own
# files via a sentinel timestamp and removes only those.
set -uo pipefail
DA="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SESS="stale-it-$$"
export DARKARCHON_TEAM="it-$$"
export POLL_INTERVAL=1 TASK_IDLE_CONFIRM=2 TASK_NOSTART_CONFIRM=30 TASK_MAX_SECONDS=90
SD="$HOME/.darkarchon/$DARKARCHON_TEAM"; IO="/tmp/darkarchon-$(id -u)"
SENTINEL="$(mktemp)"; TIDF="$(mktemp)"
mkdir -p "$SD"/{states,heartbeats,locks}
tmux new-session -d -s "$SESS" -n w1 -c /tmp 2>/dev/null
printf 'WORKER_w1_NAME="w1"\nWORKER_w1_TARGET="%s:w1"\nWORKER_w1_DIR="/tmp"\nWORKER_w1_ROLE="worker"\nWORKER_w1_KIND="claude"\n' "$SESS" > "$SD/workers-runtime.env"
hook(){ printf '{"state":"%s","detail":"","event":"t","ts_epoch":%s}\n' "$1" "$(date +%s)" > "$SD/states/w1.json"; }
( while :; do printf '{"worker":"w1","pid":%s,"last_seen_epoch":%s}\n' "$$" "$(date +%s)" > "$SD/heartbeats/w1.json"; sleep 2; done ) & BEAT=$!
disown $BEAT 2>/dev/null || true
cleanup(){ kill "$BEAT" 2>/dev/null; tmux kill-session -t "=$SESS" 2>/dev/null; rm -rf "$SD"
  T=$(cat "$TIDF" 2>/dev/null); [ -n "$T" ] && rm -f "$IO"/[pr]-"$T"*.txt
  rm -f "$SENTINEL" "$TIDF"; }
trap cleanup EXIT
pass=0; fail=0; ok(){ echo "  PASS: $1"; pass=$((pass+1)); }; no(){ echo "  FAIL: $1"; fail=$((fail+1)); }

echo "Stale-straggler race:"
hook busy
(
  # Our prompt file is the only p-*.txt newer than the sentinel.
  for _ in $(seq 1 60); do
    F=$(find "$IO" -maxdepth 1 -name 'p-*.txt' -newer "$SENTINEL" 2>/dev/null | head -1)
    [ -n "$F" ] && { basename "$F" .txt | sed 's/^p-//' > "$TIDF"; break; }
    sleep 0.25
  done
  T=$(cat "$TIDF" 2>/dev/null); [ -z "$T" ] && exit 1
  sleep 2; hook idle                            # end turn 1 empty -> triggers nudge
  for _ in $(seq 1 60); do                      # wait for the promotion, don't guess
    python3 "$DA/lib/task_store.py" --db "$SD/tasks.db" get "$T" 2>/dev/null \
      | grep -q '"attempt": 2' && break
    sleep 0.5
  done
  hook busy                                     # attempt 2 is now working
  printf 'DONE-%s-a1\nSTALE answer from attempt 1\n' "$T" > "$IO/r-$T-a1.txt"
  sleep 4                                       # several polls see a1 present
  printf 'DONE-%s-a2\nFRESH answer from attempt 2\n' "$T" > "$IO/r-$T-a2.txt"
) &
OUT=$(bash "$DA/lib/dispatch.sh" w1 "do it" 2>/tmp/st.err); RC=$?
T=$(cat "$TIDF" 2>/dev/null)

[ -n "$T" ] && ok "harness located task $T" || no "could not identify task id"
grep -q NUDGE /tmp/st.err && ok "nudge fired (attempt 2 issued)" || no "no nudge in log"
[ "$RC" -eq 0 ] && ok "exit 0" || no "exit $RC — $(tail -2 /tmp/st.err|tr '\n' ' ')"
case "$OUT" in
  *FRESH*) ok "served attempt 2's answer" ;;
  *STALE*) no "SERVED STALE ANSWER — attempt separation broken" ;;
  *)       no "unexpected output: $(printf %q "$OUT")" ;;
esac
[ -s "$IO/r-$T-a1.txt" ] && ok "straggler's a1 file still on disk, unconsumed" || no "a1 file missing"
ROW=$(python3 "$DA/lib/task_store.py" --db "$SD/tasks.db" get "$T" 2>/dev/null)
echo "$ROW" | grep -q '"attempt": 2' && ok "db records attempt=2" || no "db attempt not 2"
echo "$ROW" | grep -q "r-$T-a2.txt" && ok "db result_file retargeted to a2" || no "db result_file stale"
echo "$ROW" | grep -q "STALE" && no "STALE text reached the db" || ok "db result free of stale text"
echo "--- $pass passed, $fail failed ---"; [ "$fail" -eq 0 ]
