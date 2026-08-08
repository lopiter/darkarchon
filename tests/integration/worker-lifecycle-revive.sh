#!/usr/bin/env bash
# The leave → recall lifecycle: deregister-worker.sh, prune-workers.sh,
# lib/leave-team.sh and revive-worker.sh.
#
# What this is really guarding: none of these commands may kill a tmux window.
# The bug that motivated them was a dead worker whose window had been reused for
# a human's own claude session, where the only cleanup command available
# (kill-worker.sh) would have destroyed it.
set -uo pipefail
DA="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export DARKARCHON_TEAM="lcit-$$"
SD="$HOME/.darkarchon/$DARKARCHON_TEAM"; SESS="$DARKARCHON_TEAM"
mkdir -p "$SD/states"
WORKDIR="$(mktemp -d)"
tmux new-session -d -s "$SESS" -n keeper -c /tmp 2>/dev/null
tmux new-window -t "=$SESS:" -n alpha -c /tmp
cleanup(){ tmux kill-session -t "=$SESS" 2>/dev/null; rm -rf "$SD" "$WORKDIR"; }
trap cleanup EXIT

p=0; f=0; ok(){ echo "  PASS: $1"; p=$((p+1)); }; no(){ echo "  FAIL: $1"; f=$((f+1)); }

register_alpha() {
    printf 'WORKER_alpha_NAME="alpha"\nWORKER_alpha_TARGET="%s:alpha"\nWORKER_alpha_DIR="%s"\nWORKER_alpha_ROLE="backend"\nWORKER_alpha_KIND="claude"\n' \
        "$SESS" "$WORKDIR" > "$SD/workers-runtime.env"
}
hook_state() { printf '{"state":"%s","detail":"","session_id":"%s"}' "$1" "${2:-}" > "$SD/states/alpha.json"; }

echo "deregister-worker.sh:"
register_alpha
hook_state busy
"$DA/lib/deregister-worker.sh" alpha >/dev/null 2>&1
[ $? -eq 2 ] && ok "refuses a live worker (exit 2)" || no "did not refuse a live worker"
grep -q WORKER_alpha_TARGET "$SD/workers-runtime.env" && ok "live worker stays registered" || no "live worker was deregistered"

hook_state ended "aaaa-1111-bbbb-2222"
"$DA/lib/deregister-worker.sh" alpha --quiet
[ $? -eq 0 ] && ok "removes a dead worker" || no "failed on a dead worker"
grep -q WORKER_alpha_TARGET "$SD/workers-runtime.env" && no "registration survived" || ok "registration stripped"
tmux list-windows -t "=$SESS" -F '#W' | grep -qx alpha && ok "tmux window left alive" || no "window was killed"
[ -f "$SD/departed/alpha.json" ] && ok "leaves a recall record" || no "no recall record"
python3 -c "
import json,sys
r=json.load(open('$SD/departed/alpha.json'))
sys.exit(0 if r['cwd']=='$WORKDIR' and r['role']=='backend' and r['kind']=='claude'
         and r['session_id']=='aaaa-1111-bbbb-2222' and r['reason']=='deregistered' else 1)" \
  && ok "record carries cwd/role/kind and the session id" || no "record contents wrong"

echo "revive-worker.sh:"
OUT="$("$DA/revive-worker.sh" alpha --dry-run 2>&1)"
[ $? -eq 0 ] && echo "$OUT" | grep -q -- "--resume-session aaaa-1111-bbbb-2222" \
  && ok "plans a resume from the recall record alone" || no "dry-run plan wrong: $OUT"
echo "$OUT" | grep -q "rename old window" && ok "plans to rename the old window, not kill it" || no "no rename in plan"

OUT="$("$DA/revive-worker.sh" alpha --fresh --dry-run 2>&1)"
echo "$OUT" | grep -q "fresh — no conversation" && ok "--fresh plans a clean spawn" || no "--fresh plan wrong: $OUT"

"$DA/revive-worker.sh" alpha --fresh --session-id x >/dev/null 2>&1
[ $? -eq 1 ] && ok "--fresh with --session-id is refused" || no "contradictory flags accepted"

"$DA/revive-worker.sh" alpha --adopt --dry-run >/dev/null 2>&1
[ $? -eq 3 ] && ok "--adopt refuses a pane with no agent in it (exit 3)" || no "--adopt accepted a bare shell"

register_alpha
hook_state busy
"$DA/revive-worker.sh" alpha --dry-run >/dev/null 2>&1
[ $? -eq 2 ] && ok "refuses to revive a live worker (exit 2)" || no "revived a live worker"

# Dead, but with no conversation to go back to (predates the state hook, or an
# invited worker that never had one).
rm -f "$SD/departed/alpha.json"
register_alpha
hook_state ended
"$DA/revive-worker.sh" alpha --dry-run >/dev/null 2>&1
[ $? -eq 1 ] && ok "refuses when no session was ever recorded" || no "resumed a worker with no session id"

echo "unknown worker:"
"$DA/revive-worker.sh" nobody --dry-run >/dev/null 2>&1
[ $? -eq 1 ] && ok "unknown name -> exit 1" || no "unknown name accepted"

echo "leave-team.sh (worker resigns):"
register_alpha
hook_state idle "cccc-3333"
EE_WORKER_NAME=alpha "$DA/lib/leave-team.sh" --reason context-full --handover - >/dev/null 2>&1 <<'EOF'
Half of the migration is applied; the rollback script is untested.
EOF
[ $? -eq 0 ] && ok "worker can deregister itself" || no "leave-team failed"
grep -q WORKER_alpha_TARGET "$SD/workers-runtime.env" && no "still registered after leaving" || ok "registration released"
tmux list-windows -t "=$SESS" -F '#W' | grep -qx alpha && ok "its pane keeps running" || no "leaving killed the pane"
grep -q "rollback script is untested" "$SD/handovers/alpha.md" && ok "handover note written" || no "no handover note"
ls "$SD"/questions/*.json >/dev/null 2>&1 && grep -ql "LEFT THE TEAM" "$SD"/questions/*.json \
  && ok "the orchestrator is told" || no "departure not filed on the question queue"
python3 -c "
import json,sys
r=json.load(open('$SD/departed/alpha.json'))
sys.exit(0 if r['reason']=='left:context-full' and r['session_id']=='cccc-3333' else 1)" \
  && ok "recall record notes why it left" || no "leave reason not recorded"

# The replacement reads the note from the path the launcher computes — same
# sanitization on both sides, or the handover silently never gets picked up.
LAUNCHER_PATH="$SD/handovers/$(printf '%s' alpha | tr -c '[:alnum:]_' '_').md"
[ -f "$LAUNCHER_PATH" ] && ok "note sits where start-worker-claude.sh looks for it" || no "handover path mismatch"

echo "prune-workers.sh:"
register_alpha
hook_state ended
# Captured, not piped: grep -q closes the pipe on its first match, and under
# `set -o pipefail` the SIGPIPE'd producer would fail the whole assertion.
OUT="$("$DA/prune-workers.sh" --dry-run 2>&1)"
echo "$OUT" | grep -q "alpha" && ok "dry-run lists the dead worker" || no "dry-run missed it: $OUT"
grep -q WORKER_alpha_TARGET "$SD/workers-runtime.env" && ok "dry-run changes nothing" || no "dry-run pruned anyway"
"$DA/prune-workers.sh" --yes >/dev/null
grep -q WORKER_alpha_TARGET "$SD/workers-runtime.env" && no "prune left the ghost" || ok "prunes the dead registration"
tmux list-windows -t "=$SESS" -F '#W' | grep -qx alpha && ok "prune never kills a window" || no "prune killed a window"

echo
echo "passed=$p failed=$f"
[ "$f" -eq 0 ]
