#!/usr/bin/env bash
# lib/dispatch.sh trigger delivery over the cross-session messaging socket.
# Same harness style as dispatch-result-header.sh — real tmux window, real
# STATE_DIR, we play the worker — but the states file records a messaging
# socket, so the trigger must arrive there as a JSON line and the pane must
# stay untouched. The no-socket path is covered by the other dispatch tests,
# whose states files never record one.
set -uo pipefail
DA="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SESS="dispsock-it-$$"
export DARKARCHON_TEAM="dsit-$$"
export POLL_INTERVAL=1 TASK_IDLE_CONFIRM=2 TASK_NOSTART_CONFIRM=6 TASK_MAX_SECONDS=45
SD="$HOME/.darkarchon/$DARKARCHON_TEAM"
IO="/tmp/darkarchon-$(id -u)"        # shared with real dispatches — never rm -rf
mkdir -p "$SD"/{states,heartbeats,locks}
SOCK="$SD/inbox.sock"
CAP="$SD/captured.jsonl"

tmux new-session -d -s "$SESS" -n w1 -c /tmp 2>/dev/null
printf 'WORKER_w1_NAME="w1"\nWORKER_w1_TARGET="%s:w1"\nWORKER_w1_DIR="/tmp"\nWORKER_w1_ROLE="worker"\nWORKER_w1_KIND="claude"\n' "$SESS" > "$SD/workers-runtime.env"

hook(){ printf '{"state":"%s","detail":"","event":"t","ts_epoch":%s,"messaging_socket":"%s","session_id":"sid-disp"}\n' \
        "$1" "$(date +%s)" "$SOCK" > "$SD/states/w1.json"; }
beat(){ printf '{"worker":"w1","pid":%s,"last_seen_epoch":%s}\n' "$$" "$(date +%s)" > "$SD/heartbeats/w1.json"; }
( while :; do beat; sleep 2; done ) & BEAT=$!
disown $BEAT 2>/dev/null || true

# Fake inbox: accept connections forever, append each received line to $CAP.
python3 - "$SOCK" "$CAP" <<'PY' &
import socket, sys
srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
srv.bind(sys.argv[1])
srv.listen(4)
while True:
    conn, _ = srv.accept()
    conn.settimeout(10)
    buf = b""
    try:
        while True:
            d = conn.recv(65536)
            if not d:
                break
            buf += d
    except Exception:
        pass
    with open(sys.argv[2], "ab") as fh:
        fh.write(buf)
    conn.close()
PY
SRV=$!
disown $SRV 2>/dev/null || true

MINE=()
cleanup(){ kill "$BEAT" "$SRV" 2>/dev/null; tmux kill-session -t "=$SESS" 2>/dev/null
           rm -rf "$SD"; for f in "${MINE[@]:-}"; do [ -n "$f" ] && rm -f "$f"; done; }
trap cleanup EXIT
beat
for _ in $(seq 50); do [ -S "$SOCK" ] && break; sleep 0.1; done
[ -S "$SOCK" ] || { echo "FATAL: fake inbox socket never bound"; exit 1; }

pass=0; fail=0
ok(){ echo "  PASS: $1"; pass=$((pass+1)); }
no(){ echo "  FAIL: $1"; fail=$((fail+1)); }
tid(){ basename "$(ls -t "$IO"/p-*.txt 2>/dev/null | head -1)" .txt | sed 's/^p-//'; }
rpath(){ echo "$IO/r-$(tid)-a$1.txt"; }
marker(){ echo "DONE-$(tid)-a$1"; }

echo "T1: trigger travels over the socket, not the pane"
hook busy
( sleep 3; printf '%s\nsocket answer\n' "$(marker 1)" > "$(rpath 1)" ) &
OUT=$(bash "$DA/lib/dispatch.sh" w1 "do it" 2>/tmp/dsit.err); RC=$?
MINE+=("$(rpath 1)")
[ "$RC" -eq 0 ] && ok "exit 0" || no "exit $RC — $(tail -2 /tmp/dsit.err | tr '\n' ' ')"
[ "$OUT" = "socket answer" ] && ok "result returned" || no "got $(printf %q "$OUT")"
[ -s "$CAP" ] && ok "trigger captured on socket" || no "nothing on socket"
jq -e '.type == "user" and .session_id == "sid-disp"' "$CAP" >/dev/null 2>&1 \
    && ok "user message with session_id guard" || no "wire format wrong: $(head -c 200 "$CAP")"
jq -r '.message.content' "$CAP" 2>/dev/null | grep -q "^Read $IO/p-.* then Write final answer to .*-a1.txt then output DONE-.*-a1$" \
    && ok "content is the attempt-1 trigger" || no "content wrong: $(jq -r .message.content "$CAP" 2>/dev/null)"
tmux capture-pane -p -t "=$SESS:w1" | grep -q "Read $IO" \
    && no "trigger leaked into the pane" || ok "pane untouched"

echo "T2: nudge re-sends attempt-2 trigger on the socket"
: > "$CAP"
hook busy
# End the turn with no result: flip to idle so the confirm window elapses and
# dispatch nudges (~4s). Then come back busy — a real worker would on the
# attempt-2 trigger — and produce the attempt-2 result.
( sleep 2; hook idle; sleep 4; hook busy ) &
( sleep 9; printf '%s\nsecond try\n' "$(marker 2)" > "$(rpath 2)" ) &
OUT=$(bash "$DA/lib/dispatch.sh" w1 "do it again" 2>/tmp/dsit2.err); RC=$?
MINE+=("$(rpath 2)")
[ "$RC" -eq 0 ] && ok "exit 0 after nudge" || no "exit $RC — $(tail -2 /tmp/dsit2.err | tr '\n' ' ')"
[ "$OUT" = "second try" ] && ok "attempt-2 result returned" || no "got $(printf %q "$OUT")"
grep -q NUDGE /tmp/dsit2.err && ok "nudge happened" || no "no nudge recorded"
[ "$(grep -c '"type"' "$CAP")" -eq 2 ] && ok "both triggers on socket" || no "expected 2 socket lines, got $(grep -c '"type"' "$CAP")"
tail -1 "$CAP" | jq -r '.message.content' 2>/dev/null | grep -q -- "-a2" \
    && ok "second trigger is attempt-scoped" || no "attempt-2 trigger wrong"

echo "--- $pass passed, $fail failed ---"
[ "$fail" -eq 0 ]
