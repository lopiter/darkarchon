#!/usr/bin/env bash
# mailbox.sh notify over the cross-session messaging socket: a claude worker
# whose states file records a messaging_socket gets its MAILBOX_NOTIFY trigger
# as one newline-terminated JSON line on that socket — no tmux involved (the
# registered target deliberately names a session that doesn't exist). The
# no-socket fallback to send-keys is covered by mailbox-groups-and-ack.sh,
# whose workers have no states files.
set -uo pipefail
DA="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export DARKARCHON_TEAM="mbsock-$$"
SD="$HOME/.darkarchon/$DARKARCHON_TEAM"
mkdir -p "$SD/states"
SOCK="$SD/inbox.sock"
CAP="$SD/captured.json"
cleanup(){ kill "$SRV" 2>/dev/null; rm -rf "$SD"; }
trap cleanup EXIT

p=0; f=0; ok(){ echo "  PASS: $1"; p=$((p+1)); }; no(){ echo "  FAIL: $1"; f=$((f+1)); }

# Fake receiving session: accept one connection, capture the first line.
python3 - "$SOCK" "$CAP" <<'PY' &
import socket, sys
srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
srv.bind(sys.argv[1])
srv.listen(1)
srv.settimeout(15)
conn, _ = srv.accept()
conn.settimeout(15)
buf = b""
while b"\n" not in buf:
    d = conn.recv(65536)
    if not d:
        break
    buf += d
with open(sys.argv[2], "wb") as fh:
    fh.write(buf.split(b"\n")[0])
conn.close()
PY
SRV=$!
for _ in $(seq 50); do [ -S "$SOCK" ] && break; sleep 0.1; done
[ -S "$SOCK" ] || { echo "FATAL: fake inbox socket never bound"; exit 1; }

printf 'WORKER_w_NAME="w"\nWORKER_w_TARGET="no-such-tmux-sess:0"\nWORKER_w_DIR="/tmp/x"\nWORKER_w_KIND="claude"\n' \
    > "$SD/workers-runtime.env"
printf '{"state":"idle","messaging_socket":"%s","session_id":"sid-123"}\n' "$SOCK" \
    > "$SD/states/w.json"

echo "Socket notify:"
"$DA/lib/mailbox.sh" send w --from boss "hello over the wire" >/dev/null \
    && ok "send succeeds with no live tmux target" || no "send failed"
wait "$SRV" 2>/dev/null
[ -s "$CAP" ] && ok "trigger arrived on the socket" || no "nothing captured on socket"

echo "Wire format:"
jq -e '.type == "user"' "$CAP" >/dev/null 2>&1 \
    && ok 'type == "user"' || no "type wrong: $(cat "$CAP")"
jq -e '.from == "boss"' "$CAP" >/dev/null 2>&1 \
    && ok "from carries the sender" || no "from wrong"
jq -e '.session_id == "sid-123"' "$CAP" >/dev/null 2>&1 \
    && ok "session_id guard included from states file" || no "session_id missing/wrong"
jq -e '.message.content | test("MAILBOX_NOTIFY from=boss")' "$CAP" >/dev/null 2>&1 \
    && ok "content is the mailbox trigger" || no "content wrong: $(jq -r .message.content "$CAP" 2>/dev/null)"

echo "Bookkeeping:"
[ "$("$DA/lib/mailbox.sh" count w)" = "1" ] \
    && ok "message landed in the mailbox regardless of transport" || no "mailbox count wrong"

echo "--- $p passed, $f failed ---"
[ "$f" -eq 0 ]
