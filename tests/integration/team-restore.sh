#!/usr/bin/env bash
# restore-team.sh: bring every dead worker of a team back in one command.
#
# Only --dry-run is exercised — the real path ends in spawn-worker.sh launching
# a claude process, which is revive-worker.sh's job and covered there. What this
# guards is the selection: dead workers are revived, live ones are left alone,
# and a worker with no recorded conversation is respawned fresh rather than
# refused (after a reboot, refusing one worker must not abort the team).
set -uo pipefail
DA="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export DARKARCHON_TEAM="rsit-$$"
SD="$HOME/.darkarchon/$DARKARCHON_TEAM"; SESS="$DARKARCHON_TEAM"
mkdir -p "$SD/states"
WORKDIR="$(mktemp -d)"
# gamma's session is alive; alpha and beta point at a session that no longer
# exists, the way every worker does after a reboot.
tmux new-session -d -s "$SESS" -n gamma -c /tmp 2>/dev/null
cleanup(){ tmux kill-session -t "=$SESS" 2>/dev/null; rm -rf "$SD" "$WORKDIR"; }
trap cleanup EXIT

p=0; f=0; ok(){ echo "  PASS: $1"; p=$((p+1)); }; no(){ echo "  FAIL: $1"; f=$((f+1)); }

reg() { # name target [extra-lines]
    printf 'WORKER_%s_NAME="%s"\nWORKER_%s_TARGET="%s"\nWORKER_%s_DIR="%s"\nWORKER_%s_ROLE="backend"\nWORKER_%s_KIND="claude"\n%s\n' \
        "$1" "$1" "$1" "$2" "$1" "$WORKDIR" "$1" "$1" "${3:-}" >> "$SD/workers-runtime.env"
}
hook_state() { printf '{"state":"%s","detail":"","session_id":"%s"}' "$2" "${3:-}" > "$SD/states/$1.json"; }

echo "empty team:"
OUT="$("$DA/restore-team.sh" --dry-run 2>&1)"
[ $? -eq 0 ] && echo "$OUT" | grep -qi "nothing to restore" && ok "no registry -> nothing to restore, exit 0" || no "empty team: $OUT"

echo "selection:"
: > "$SD/workers-runtime.env"
reg alpha "$SESS-gone:alpha"
hook_state alpha ended "aaaa-1111"
reg beta "$SESS-gone:beta" 'WORKER_beta_EXTERNAL=1'
reg gamma "$SESS:gamma"
hook_state gamma busy "cccc-3333"

OUT="$("$DA/restore-team.sh" --dry-run 2>&1)"; RC=$?
[ $RC -eq 0 ] && ok "dry-run exits 0" || no "dry-run exit $RC: $OUT"
echo "$OUT" | grep -q "alpha.*resume aaaa-1111" && ok "dead worker with a session id is resumed" || no "alpha plan wrong: $OUT"
echo "$OUT" | grep -q "beta.*fresh" && ok "dead worker without a session id is respawned fresh" || no "beta plan wrong: $OUT"
echo "$OUT" | grep -q "gamma.*skip" && ok "live worker is skipped" || no "gamma plan wrong: $OUT"
grep -q 'WORKER_alpha_TARGET' "$SD/workers-runtime.env" && ok "dry-run changes nothing" || no "dry-run touched the registry"

OUT="$("$DA/restore-team.sh" --dry-run --fresh 2>&1)"
echo "$OUT" | grep -q "alpha.*fresh" && ok "--fresh drops the conversation for every worker" || no "--fresh plan wrong: $OUT"
echo "$OUT" | grep -q "resume" && no "--fresh still plans a resume" || ok "--fresh plans no resume"

OUT="$("$DA/restore-team.sh" --dry-run --only beta 2>&1)"
echo "$OUT" | grep -q "beta" && ! echo "$OUT" | grep -q "alpha" && ok "--only narrows to the named worker" || no "--only plan wrong: $OUT"

"$DA/restore-team.sh" --dry-run --only nobody >/dev/null 2>&1
[ $? -eq 1 ] && ok "--only with an unknown name -> exit 1" || no "unknown --only accepted"

"$DA/restore-team.sh" --bogus >/dev/null 2>&1
[ $? -eq 1 ] && ok "unknown flag -> exit 1" || no "unknown flag accepted"

echo "all alive:"
: > "$SD/workers-runtime.env"
reg gamma "$SESS:gamma"
hook_state gamma busy "cccc-3333"
OUT="$("$DA/restore-team.sh" --dry-run 2>&1)"
[ $? -eq 0 ] && echo "$OUT" | grep -qi "nothing to restore" && ok "every worker alive -> nothing to restore" || no "all-alive: $OUT"

echo
echo "passed=$p failed=$f"
[ "$f" -eq 0 ]
