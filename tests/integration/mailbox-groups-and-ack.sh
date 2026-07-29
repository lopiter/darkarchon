#!/usr/bin/env bash
# lib/mailbox.sh: group addressing, read_at stamping, outstanding/renotify, and
# the MCP tool's delegation to this script (which is what fixes the notification
# that used to be left unsent on the recipient's prompt line).
set -uo pipefail
DA="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export DARKARCHON_TEAM="mbit-$$"
SD="$HOME/.darkarchon/$DARKARCHON_TEAM"; SESS="mbit-$$"
mkdir -p "$SD"
tmux new-session -d -s "$SESS" -n a -c /tmp 2>/dev/null
tmux new-window -t "=$SESS:" -n b -c /tmp
cleanup(){ tmux kill-session -t "=$SESS" 2>/dev/null; rm -rf "$SD"; }
trap cleanup EXIT
printf 'WORKER_a_NAME="a"\nWORKER_a_TARGET="%s:a"\nWORKER_a_DIR="/tmp/x"\nWORKER_a_KIND="claude"\nWORKER_b_NAME="b"\nWORKER_b_TARGET="%s:b"\nWORKER_b_DIR="/tmp/x"\nWORKER_b_KIND="codex"\n' "$SESS" "$SESS" > "$SD/workers-runtime.env"
MB="$DA/lib/mailbox.sh"
p=0; f=0; ok(){ echo "  PASS: $1"; p=$((p+1)); }; no(){ echo "  FAIL: $1"; f=$((f+1)); }

echo "Group addressing:"
"$MB" send @bogus --from a x >/dev/null 2>&1; [ $? -eq 1 ] && ok "unknown group -> exit 1" || no "unknown group exit code"
"$MB" send @cwd:/nope --from a x >/dev/null 2>&1; [ $? -eq 4 ] && ok "empty group -> exit 4" || no "empty group exit code"
OUT=$("$MB" send @all --from a "broadcast")
[ "$(echo "$OUT" | wc -l | tr -d ' ')" = "1" ] && echo "$OUT" | grep -q "to=b" \
  && ok "@all reaches peers and excludes the sender" || no "@all expansion wrong: $OUT"
"$MB" send @codex --from a "codex only" | grep -q "to=b" && ok "@codex filters by kind" || no "@codex wrong"
[ -z "$("$MB" send @claude --from a "claude only" 2>/dev/null)" ] && ok "@claude excludes sender, matches nobody -> exit 4" || no "@claude unexpectedly delivered"

echo "Delivery bookkeeping:"
"$MB" clear b >/dev/null; "$MB" send b --from a "m1" >/dev/null
[ -z "$("$MB" outstanding b)" ] && ok "fresh message is not outstanding" || no "fresh message listed as outstanding"
[ -n "$("$MB" outstanding b --older-than 0)" ] && ok "--older-than 0 lists it" || no "--older-than 0 empty"
"$MB" renotify b | grep -q "undrained" && ok "renotify reports what is queued" || no "renotify output"
"$MB" read b >/dev/null
python3 -c "
import json,sys
r=[json.loads(l) for l in open('$SD/mailboxes/b.drained.jsonl')][-1]
sys.exit(0 if r.get('read_at') and r.get('created_at') else 1)" \
  && ok "drained message keeps created_at and gains read_at" || no "read_at/created_at wrong"
[ "$("$MB" count b)" = "0" ] && ok "pending emptied after read" || no "pending not emptied"
"$MB" renotify b | grep -q "nothing outstanding" && ok "renotify is a no-op when empty" || no "renotify when empty"

echo "MCP delegation:"
# A real worker inherits EE_STATE_DIR but NOT DARKARCHON_TEAM, so unset it here.
# With it set, the delegated scripts resolve the right state dir for the wrong
# reason and this test cannot catch a regression in that resolution.
env -u DARKARCHON_TEAM EE_WORKER_NAME=a EE_STATE_DIR="$SD" python3 - "$DA" <<'PY' >/dev/null 2>&1
import importlib.util,sys
spec=importlib.util.spec_from_file_location("m",sys.argv[1]+"/lib/mcp_server.py")
m=importlib.util.module_from_spec(spec)
try: spec.loader.exec_module(m)
except ImportError: sys.exit(0)
getattr(m.mailbox_send,'fn',m.mailbox_send)("b","via mcp")
PY
sleep 1
if [ "$("$MB" count b)" = "1" ]; then ok "MCP send wrote through mailbox.sh"; else no "MCP send did not deliver"; fi
LAST=$(tmux capture-pane -p -t "=$SESS:b" | grep -v '^$' | tail -1)
case "$LAST" in
  *MAILBOX_NOTIFY*) no "trigger left unsent on the prompt line (missing Enter)" ;;
  *)                ok "trigger was submitted, prompt line clear" ;;
esac
echo "--- $p passed, $f failed ---"; [ "$f" -eq 0 ]
