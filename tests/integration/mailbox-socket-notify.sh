#!/usr/bin/env bash
# mailbox.sh notify over the cross-session messaging socket: a claude worker
# whose states file records a messaging_socket gets its MAILBOX_NOTIFY trigger
# as one newline-terminated JSON line on that socket — no tmux involved (the
# registered target deliberately names a session that doesn't exist). The
# no-socket fallback to send-keys is covered by mailbox-groups-and-ack.sh,
# whose workers have no states files.
#
# Also covers trigger uniqueness: Claude Code drops a message identical to one
# it just accepted from the same sender, so every socket payload must differ.
set -uo pipefail
DA="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export DARKARCHON_TEAM="mbsock-$$"
SD="$HOME/.darkarchon/$DARKARCHON_TEAM"
mkdir -p "$SD/states"
SOCK="$SD/inbox.sock"
CAP="$SD/captured.jsonl"
: > "$CAP"
cleanup(){ kill "$SRV" 2>/dev/null; rm -rf "$SD"; }
trap cleanup EXIT

p=0; f=0; ok(){ echo "  PASS: $1"; p=$((p+1)); }; no(){ echo "  FAIL: $1"; f=$((f+1)); }

# Fake receiving session: accept connections forever, append each line to $CAP.
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
for _ in $(seq 50); do [ -S "$SOCK" ] && break; sleep 0.1; done
[ -S "$SOCK" ] || { echo "FATAL: fake inbox socket never bound"; exit 1; }

# wait_lines <n> — block until $CAP holds n captured payloads (or give up).
wait_lines(){ for _ in $(seq 50); do
                  [ "$(grep -c '"type"' "$CAP" 2>/dev/null || echo 0)" -ge "$1" ] && return 0
                  sleep 0.1
              done; return 1; }
line(){ sed -n "${1}p" "$CAP"; }

printf 'WORKER_w_NAME="w"\nWORKER_w_TARGET="no-such-tmux-sess:0"\nWORKER_w_DIR="/tmp/x"\nWORKER_w_KIND="claude"\n' \
    > "$SD/workers-runtime.env"
printf '{"state":"idle","messaging_socket":"%s","session_id":"sid-123"}\n' "$SOCK" \
    > "$SD/states/w.json"

echo "Socket notify:"
"$DA/lib/mailbox.sh" send w --from boss "hello over the wire" >/dev/null \
    && ok "send succeeds with no live tmux target" || no "send failed"
wait_lines 1 && ok "trigger arrived on the socket" || no "nothing captured on socket"

echo "Wire format:"
line 1 | jq -e '.type == "user"' >/dev/null 2>&1 \
    && ok 'type == "user"' || no "type wrong: $(line 1)"
line 1 | jq -e '.from == "boss"' >/dev/null 2>&1 \
    && ok "from carries the sender" || no "from wrong"
line 1 | jq -e '.session_id == "sid-123"' >/dev/null 2>&1 \
    && ok "session_id guard included from states file" || no "session_id missing/wrong"
line 1 | jq -e '.message.content | test("MAILBOX_NOTIFY from=boss")' >/dev/null 2>&1 \
    && ok "content is the mailbox trigger" || no "content wrong: $(line 1 | jq -r .message.content 2>/dev/null)"

echo "Bookkeeping:"
[ "$("$DA/lib/mailbox.sh" count w)" = "1" ] \
    && ok "message landed in the mailbox regardless of transport" || no "mailbox count wrong"

# Claude Code's message-loop brake drops a repeat identical to the message it
# just took from the same sender. The trigger names only sender and recipient,
# so without a per-send tag a second message — or a renotify chasing its own
# original — would be silently swallowed.
echo "Trigger uniqueness (message-loop brake):"
"$DA/lib/mailbox.sh" send w --from boss "second message, same sender" >/dev/null
wait_lines 2 && ok "second send reached the socket" || no "second send never captured"
"$DA/lib/mailbox.sh" renotify w >/dev/null
wait_lines 3 && ok "renotify reached the socket" || no "renotify never captured"

c1="$(line 1 | jq -r .message.content 2>/dev/null)"
c2="$(line 2 | jq -r .message.content 2>/dev/null)"
c3="$(line 3 | jq -r .message.content 2>/dev/null)"
[ "$c1" != "$c2" ] && ok "back-to-back sends differ" || no "identical payloads: $c1"
[ "$c3" != "$c1" ] && [ "$c3" != "$c2" ] \
    && ok "renotify differs from both originals" || no "renotify payload collides: $c3"
[ "$(printf '%s\n' "$c1" "$c2" "$c3" | grep -c '^MAILBOX_NOTIFY from=boss ')" = "3" ] \
    && ok "every payload still opens with the trigger the worker looks for" \
    || no "worker-facing prefix broke: $c1 / $c2 / $c3"

echo "--- $p passed, $f failed ---"
[ "$f" -eq 0 ]
