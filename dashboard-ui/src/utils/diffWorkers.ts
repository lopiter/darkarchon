/**
 * Pure diff between two `Host[]` snapshots — yields meaningful transitions
 * that the notification system reacts to.
 *
 * Rules:
 *   - `worker_added` / `worker_removed`: id appeared / disappeared
 *   - `awaiting`:        state changed TO awaiting_user:*
 *                        (suppressed if same tick also produced worker_added —
 *                         avoids spurious alerts on first discovery)
 *   - `rate_limited`:    state changed TO rate_limited
 *   - `dead`:            state changed TO dead (host_stale-forced or natural)
 *   - `idle`:            state changed TO idle (from anything other than
 *                        dead/unknown, so resurrecting a dead host doesn't
 *                        spam idle transitions for every worker)
 *   - `dispatch_out` / `dispatch_in`: false → true edge on the flag
 *   - `mailbox_new`:     mailboxPending increased (delta > 0)
 *
 * No-op transitions (same state, same flags) produce nothing.
 */

import type { Host, Worker, WorkerState } from '../types/domain';

export type Transition =
  | { kind: 'worker_added'; worker: Worker; host: string; team: string }
  | { kind: 'worker_removed'; workerId: string; host: string; team: string }
  | { kind: 'awaiting'; worker: Worker; host: string; team: string }
  | { kind: 'rate_limited'; worker: Worker; host: string; team: string }
  | { kind: 'dead'; worker: Worker; host: string; team: string }
  | { kind: 'idle'; worker: Worker; host: string; team: string }
  | { kind: 'dispatch_out'; worker: Worker; host: string; team: string }
  | { kind: 'dispatch_in'; worker: Worker; host: string; team: string }
  | {
      kind: 'mailbox_new';
      worker: Worker;
      host: string;
      team: string;
      delta: number;
    };

interface Located {
  worker: Worker;
  host: string;
  team: string;
}

function isAwaiting(s: WorkerState): boolean {
  // Any awaiting_user:* variant needs a human, so all of them raise the alert.
  return s.startsWith('awaiting_user:');
}

function indexWorkers(hosts: Host[]): Map<string, Located> {
  const out = new Map<string, Located>();
  for (const h of hosts) {
    for (const t of h.teams) {
      for (const w of t.workers) {
        out.set(w.id, { worker: w, host: h.id, team: t.name });
      }
    }
  }
  return out;
}

export function diffWorkers(prev: Host[], next: Host[]): Transition[] {
  const prevIdx = indexWorkers(prev);
  const nextIdx = indexWorkers(next);
  const transitions: Transition[] = [];

  // Removed
  for (const [id, loc] of prevIdx) {
    if (!nextIdx.has(id)) {
      transitions.push({
        kind: 'worker_removed',
        workerId: id,
        host: loc.host,
        team: loc.team,
      });
    }
  }

  // Added / changed
  for (const [id, loc] of nextIdx) {
    const prevLoc = prevIdx.get(id);

    if (!prevLoc) {
      transitions.push({
        kind: 'worker_added',
        worker: loc.worker,
        host: loc.host,
        team: loc.team,
      });
      // Suppress state-derived transitions on first discovery — worker_added
      // alone is enough for the dispatcher to decide what to do.
      continue;
    }

    const before = prevLoc.worker;
    const after = loc.worker;

    // State-edge transitions (only on actual change)
    if (after.state !== before.state) {
      if (isAwaiting(after.state) && !isAwaiting(before.state)) {
        transitions.push({
          kind: 'awaiting',
          worker: after,
          host: loc.host,
          team: loc.team,
        });
      }
      if (after.state === 'rate_limited' && before.state !== 'rate_limited') {
        transitions.push({
          kind: 'rate_limited',
          worker: after,
          host: loc.host,
          team: loc.team,
        });
      }
      if (after.state === 'dead' && before.state !== 'dead') {
        transitions.push({
          kind: 'dead',
          worker: after,
          host: loc.host,
          team: loc.team,
        });
      }
      const wasGone = before.state === 'dead' || before.state === 'unknown';
      if (after.state === 'idle' && !wasGone) {
        transitions.push({
          kind: 'idle',
          worker: after,
          host: loc.host,
          team: loc.team,
        });
      }
    }

    // Flag-edge transitions (false → true)
    if (after.dispatchOut && !before.dispatchOut) {
      transitions.push({
        kind: 'dispatch_out',
        worker: after,
        host: loc.host,
        team: loc.team,
      });
    }
    if (after.dispatchIn && !before.dispatchIn) {
      transitions.push({
        kind: 'dispatch_in',
        worker: after,
        host: loc.host,
        team: loc.team,
      });
    }

    // Numeric-edge: mailbox count grew
    if (after.mailboxPending > before.mailboxPending) {
      transitions.push({
        kind: 'mailbox_new',
        worker: after,
        host: loc.host,
        team: loc.team,
        delta: after.mailboxPending - before.mailboxPending,
      });
    }
  }

  return transitions;
}
