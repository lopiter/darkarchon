/**
 * "Is this team finished, and how do I shut it down?" — the logic behind the
 * team detail panel and the team context menu.
 *
 * Stopping a team is destructive and irreversible from the dashboard's side:
 * killing the tmux session takes every pane's unsaved context with it. So the
 * dashboard never runs any of this. It answers the one question that is hard
 * to answer by eye — *is anything still in flight?* — and hands over the exact
 * commands to paste, in the order they have to run.
 *
 * Everything here is pure so the ordering and the refusals stay testable.
 */

import type { Worker } from '../types/domain';

/** One reason the team is not finished yet, with how many workers cause it. */
export interface Blocker {
  kind:
    | 'working'
    | 'awaiting'
    | 'dispatch'
    | 'unreviewed'
    | 'mailbox'
    | 'rate_limited';
  /** Sentence fragment, already pluralised: "2 workers still working". */
  label: string;
  count: number;
  /** Worker names behind the count — shown so the user can go look. */
  workers: string[];
}

const plural = (n: number, one: string, many: string) =>
  `${n} ${n === 1 ? one : many}`;

/**
 * Every reason not to stop the team right now, most urgent first.
 *
 * Dead and unknown workers are not blockers — a team whose panes already died
 * is exactly the team worth tearing down. Everything else here represents work
 * or a result that killing the session would destroy: a busy pane loses its
 * turn, an awaiting pane loses the question, an unreviewed result is gone
 * before anyone reads it, and an unread mailbox message is never delivered.
 */
export function shutdownBlockers(workers: Worker[]): Blocker[] {
  const pick = (fn: (w: Worker) => boolean) => workers.filter(fn);

  const working = pick((w) => w.state === 'busy' || w.state === 'compacting');
  const awaiting = pick((w) => w.state.startsWith('awaiting_user:'));
  const dispatch = pick((w) => w.dispatchIn || w.dispatchOut);
  const unreviewed = pick((w) => w.unseenDone);
  const mailbox = pick((w) => w.mailboxPending > 0);
  const rateLimited = pick((w) => w.state === 'rate_limited');

  const out: Blocker[] = [];
  const add = (kind: Blocker['kind'], list: Worker[], label: string) => {
    if (list.length > 0) {
      out.push({ kind, label, count: list.length, workers: list.map((w) => w.name) });
    }
  };

  add('working', working, `${plural(working.length, 'worker', 'workers')} still working`);
  add('awaiting', awaiting, `${plural(awaiting.length, 'worker', 'workers')} waiting on you`);
  add('dispatch', dispatch, `${plural(dispatch.length, 'worker', 'workers')} with a dispatch in flight`);
  add('unreviewed', unreviewed, `${plural(unreviewed.length, 'result', 'results')} you haven't read`);
  add(
    'mailbox',
    mailbox,
    `${plural(
      mailbox.reduce((n, w) => n + w.mailboxPending, 0),
      'undelivered message',
      'undelivered messages'
    )}`
  );
  // Last: a rate-limited worker is stalled rather than working, so it is the
  // weakest reason to wait — but it will resume on its own, and stopping now
  // throws away whatever it was mid-way through.
  add(
    'rate_limited',
    rateLimited,
    `${plural(rateLimited.length, 'worker', 'workers')} rate limited (will resume)`
  );
  return out;
}

/** tmux session part of a `session:window[.pane]` target. */
export function sessionOf(tmuxTarget: string): string {
  return tmuxTarget.split(':')[0] ?? '';
}

/**
 * The tmux sessions this team owns — the ones its own workers were spawned
 * into.
 *
 * Invited (EXTERNAL) panes are excluded on purpose. Their session belongs to
 * whoever invited them; it very often holds unrelated windows, and killing it
 * to shut down a team would be the worst kind of surprise. `kill-worker.sh`
 * refuses them for the same reason, and the panel says so out loud.
 */
export function ownedSessions(workers: Worker[]): string[] {
  const owned = new Set<string>();
  for (const w of workers) {
    if (w.external) continue;
    const s = sessionOf(w.tmuxTarget);
    if (s) owned.add(s);
  }
  return Array.from(owned).sort();
}

/** Sessions the team borrows from someone else — named, never killed. */
export function externalSessions(workers: Worker[]): string[] {
  const ext = new Set<string>();
  for (const w of workers) {
    if (!w.external) continue;
    const s = sessionOf(w.tmuxTarget);
    if (s) ext.add(s);
  }
  return Array.from(ext).sort();
}

/** Single-quote for a POSIX shell — state dirs and session names are paths
 *  and user-chosen strings, and both can carry spaces. */
function q(s: string): string {
  return `'${s.replace(/'/g, `'\\''`)}'`;
}

export interface TeamCommands {
  /** Kill the tmux session(s) the team owns. Empty when it owns none. */
  stop: string;
  /** Drop the registrations the kill leaves behind. */
  prune: string | null;
  /** Move the state dir aside once nothing is running. */
  archive: string | null;
  /** stop → prune → archive as one pasteable block, with comments. */
  full: string;
}

/**
 * Shutdown commands for one team.
 *
 * Sessions come from the live worker targets rather than from the team name:
 * a team's name and its tmux session agree for a plain team but not for a
 * worktree team (name `myteam-feature-x`, dir `myteam/feature-x`), and a
 * kill-session aimed at a guess is not a mistake worth risking. The targets
 * are what the hub actually observed.
 *
 * prune and archive address the team by state dir for the same reason, and
 * both are omitted when the hub didn't report one (older hub, or the
 * synthetic `(unknown)` bucket).
 */
export function teamCommands(
  workers: Worker[],
  stateDir: string | undefined
): TeamCommands {
  const sessions = ownedSessions(workers);
  const stop = sessions.map((s) => `tmux kill-session -t ${q(s)}`).join('\n');
  const prune = stateDir
    ? `EE_STATE_DIR=${q(stateDir)} "$DARKARCHON_HOME/prune-workers.sh" --yes`
    : null;
  // No --yes: archive prints what it will move and asks. That prompt is the
  // last checkpoint before a state dir leaves its place, and it costs one
  // keystroke.
  const archive = stateDir
    ? `"$DARKARCHON_HOME/lib/teams.sh" archive ${q(stateDir)}`
    : null;

  const lines: string[] = [];
  if (stop) {
    lines.push('# 1. stop the team session', stop);
  } else {
    lines.push('# no session to kill — this team owns no panes of its own');
  }
  if (prune) lines.push('', '# 2. drop the registrations the kill leaves behind', prune);
  if (archive) lines.push('', '# 3. move the state dir aside (never deletes)', archive);

  return { stop, prune, archive, full: lines.join('\n') };
}
